import asyncio
import json
import re
import sqlite3
from urllib.parse import urlsplit

from . import config
from .ai import Writer
from .content import normalized, quality, sanitize
from .crawler import Crawler
from .db import now, uid, unpack
from .network import ServiceError
from .wordpress import WordPress


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, db, vault, http):
        self.db, self.vault, self.http = db, vault, http
        self.crawler = Crawler(http)

    def writer(self):
        encrypted = self.db.setting("openai_key")
        return Writer(self.vault.decrypt(encrypted) if encrypted else config.API_KEY,
                      self.db.setting("model", config.MODEL))

    def site(self, site_id):
        row = self.db.one("SELECT * FROM sites WHERE id=?", (site_id,))
        if not row:
            raise ServiceError("سایت پیدا نشد.")
        return unpack(row, ["profile", "analysis", "connection"])

    def wp(self, site):
        if site["demo"]:
            raise ServiceError("سایت نمونه به وردپرس واقعی متصل نمی‌شود.")
        if not site["credentials"]:
            raise ServiceError("ابتدا اتصال وردپرس را تنظیم و آزمایش کنید.")
        credentials = json.loads(self.vault.decrypt(site["credentials"]))
        return WordPress(self.http, site["url"], credentials["username"], credentials["password"])

    def enqueue(self, site_id, kind, payload=None):
        job_id = uid()
        try:
            self.db.execute("INSERT INTO jobs(id,site_id,kind,payload,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                            (job_id, site_id, kind, json.dumps(payload or {}), "در صف پردازش", now(), now()))
        except sqlite3.IntegrityError as e:
            raise ServiceError("یک عملیات برای این سایت در حال اجراست؛ منتظر پایان آن بمانید.") from e
        return {"id": job_id}

    async def progress(self, job_id, percent, message):
        row = self.db.one("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))
        if row and row["cancel_requested"]:
            raise Cancelled()
        self.db.execute("UPDATE jobs SET progress=?,message=?,updated_at=? WHERE id=?", (percent, message, now(), job_id))

    async def run(self):
        while True:
            job = self.db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
            if not job:
                await asyncio.sleep(0.5)
                continue
            if job["cancel_requested"]:
                self.db.execute("UPDATE jobs SET status='cancelled',message='لغو شد',updated_at=? WHERE id=?", (now(), job["id"]))
                continue
            self.db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), job["id"]))
            try:
                await self.handle(job)
                self.db.execute("UPDATE jobs SET status='completed',progress=100,message='با موفقیت انجام شد',updated_at=? WHERE id=?", (now(), job["id"]))
                self.db.event(job["site_id"], {"analyze": "تحلیل سایت و پیشنهاد موضوع انجام شد", "generate": "مقاله آماده بازبینی شد",
                                             "pipeline": "جریان تولید محتوا تکمیل شد", "sync": "همگام‌سازی وردپرس انجام شد"}[job["kind"]])
            except Cancelled:
                self.db.execute("UPDATE jobs SET status='cancelled',message='لغو شد',updated_at=? WHERE id=?", (now(), job["id"]))
            except Exception as e:
                error = str(e) if isinstance(e, (ServiceError, ValueError)) else "خطای داخلی در پردازش رخ داد؛ هیچ عملیات انتشار خودکاری تکرار نشد."
                self.db.execute("UPDATE jobs SET status='failed',error=?,message='عملیات ناموفق بود',updated_at=? WHERE id=?", (error[:500], now(), job["id"]))
                self.db.event(job["site_id"], "عملیات ناموفق بود: " + error[:300])

    async def handle(self, job):
        site = self.site(job["site_id"])
        if site["demo"]:
            raise ServiceError("برای عملیات واقعی، سایت خود را اضافه کنید.")
        payload = json.loads(job["payload"])

        async def progress(percent, message):
            await self.progress(job["id"], percent, message)

        if job["kind"] in ("analyze", "pipeline"):
            await progress(5, "بررسی دسترسی و نقشه سایت")
            analysis = await self.crawler.scan(site["url"], progress)
            await progress(62, "شناسایی موضوعات مرتبط با کسب‌وکار")
            titles = [r["title"] for r in self.db.all("SELECT title FROM articles WHERE site_id=?", (site["id"],))]
            plan = await self.writer().plan(site["profile"], analysis, titles)
            analysis.update({"summary": plan["summary"], "audience": plan["audience"], "analyzed_at": now()})
            self.db.execute("UPDATE sites SET analysis=? WHERE id=?", (json.dumps(analysis, ensure_ascii=False), site["id"]))
            # Do not discard earlier proposals or topics with articles.
            for t in plan["topics"][:8]:
                if normalized(t["title"]) in {normalized(p["title"]) for p in analysis["pages"]}:
                    continue
                self.db.execute("INSERT OR IGNORE INTO topics(id,site_id,title,keyword,intent,rationale,outline) VALUES(?,?,?,?,?,?,?)",
                                (uid(), site["id"], t["title"][:200], normalized(t["keyword"])[:120], t["intent"],
                                 t["rationale"][:1500], json.dumps(t["outline"], ensure_ascii=False)))
            site["analysis"] = analysis
            if job["kind"] == "pipeline":
                topics = self.db.all("SELECT * FROM topics WHERE site_id=? AND status='idea' LIMIT ?", (site["id"], payload["count"]))
                if not topics:
                    raise ServiceError("موضوع جدیدی پیدا نشد؛ موضوع دلخواه خود را اضافه کنید.")
                for index, topic in enumerate(topics):
                    await progress(70, f"تولید مقاله {index + 1} از {len(topics)}")
                    article = await self.generate(site, topic, [], progress)
                    if payload.get("upload_drafts"):
                        await progress(95, "ارسال پیش‌نویس به وردپرس")
                        await self.sync(site, article, {"status": "draft"})
        elif job["kind"] == "generate":
            topic = self.db.one("SELECT * FROM topics WHERE id=? AND site_id=?", (payload["topic_id"], site["id"]))
            if not topic:
                raise ServiceError("موضوع پیدا نشد.")
            await self.generate(site, topic, payload.get("sources", []), progress)
        elif job["kind"] == "sync":
            article = self.db.one("SELECT * FROM articles WHERE id=? AND site_id=?", (payload["article_id"], site["id"]))
            await progress(30, "بررسی نسخه مقاله و دسترسی وردپرس")
            if not article or article["revision"] != payload["revision"]:
                raise ServiceError("مقاله ویرایش شده است؛ نسخه جدید را بازبینی کنید.")
            await self.sync(site, article, payload)

    async def generate(self, site, topic, source_urls, progress):
        if not site["analysis"].get("pages"):
            raise ServiceError("ابتدا سایت را تحلیل کنید.")
        if self.db.one("SELECT id FROM articles WHERE topic_id=?", (topic["id"],)):
            raise ServiceError("برای این موضوع قبلاً مقاله تولید شده است.")
        count = self.db.one("SELECT COUNT(*) n FROM articles WHERE site_id=? AND created_at>=?", (site["id"], now()[:10]))["n"]
        if count >= 10:
            raise ServiceError("سقف روزانه ۱۰ مقاله برای این سایت پر شده است.")
        sources = []
        for url in source_urls[:4]:
            await progress(20, "خواندن منابع تکمیلی")
            page = await self.crawler.page(url)
            page.pop("links", None)
            sources.append(page)
        await progress(75, "تهیه ساختار و نگارش مقاله؛ ممکن است چند دقیقه طول بکشد")
        existing = [p["title"] for p in site["analysis"]["pages"]]
        existing += [r["title"] for r in self.db.all("SELECT title FROM articles WHERE site_id=?", (site["id"],))]
        context_topic = unpack(topic, ["outline"])
        draft = await self.writer().draft(site["profile"], site["analysis"], context_topic, sources, existing)
        await progress(90, "بررسی ساختار، منابع و محتوای تکراری")
        allowed = {p["url"] for p in site["analysis"]["pages"] + sources}
        draft["sources"] = [s for s in draft["sources"] if s["url"] in allowed]
        # Attribute callback also covers alternate attribute order and entity-encoded URLs.
        draft["content_html"] = sanitize(draft["content_html"], allowed_urls=allowed)
        draft["keyword"] = topic["keyword"]
        report = quality(draft, existing)
        report["warnings"].extend(draft["review_notes"][:10])
        article_id = uid()
        slug = re.sub(r"[^a-z0-9-]", "", draft["slug"].lower())[:100] or "article-" + article_id[:12]
        self.db.execute("""INSERT INTO articles(id,site_id,topic_id,title,keyword,slug,meta_description,content_html,
                        excerpt,brief,sources,quality,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (article_id, site["id"], topic["id"], draft["title"], topic["keyword"], slug,
                         draft["meta_description"], draft["content_html"], draft["excerpt"], draft["brief"],
                         json.dumps(draft["sources"], ensure_ascii=False), json.dumps(report, ensure_ascii=False), now(), now()))
        self.db.execute("UPDATE topics SET status='drafted' WHERE id=?", (topic["id"],))
        return self.db.one("SELECT * FROM articles WHERE id=?", (article_id,))

    async def sync(self, site, article, payload):
        if article["status"] == "uncertain":
            # Recover the remote ID before any subsequent write.
            remote = await self.wp(site).find("seoagent-" + article["id"])
            if remote:
                self.db.execute("UPDATE articles SET wp_id=?,wp_url=?,remote_status=?,status=? WHERE id=?",
                                (remote["id"], remote.get("link", ""), remote["status"],
                                 {"publish": "published", "future": "scheduled"}.get(remote["status"], "wp_draft"), article["id"]))
                # Recovery is read-only. A fresh explicit action is needed to change the remote post.
                return
            raise ServiceError("نتیجه ارسال قبلی نامشخص است و نوشته پیدا نشد؛ قبل از تلاش مجدد پنل وردپرس را بررسی کنید.")
        report = json.loads(article["quality"])
        if payload["status"] != "draft":
            if article["approved_revision"] != article["revision"] or not report["can_publish"]:
                raise ServiceError("نسخه فعلی مقاله باید بازبینی و تأیید شود و خطاهای مسدودکننده نداشته باشد.")
        wp = self.wp(site)
        # Check credentials/capabilities before marking an external operation as started.
        connection = await wp.connect()
        if payload["status"] != "draft" and not connection["can_publish"]:
            raise ServiceError("کاربر وردپرس مجوز انتشار ندارد.")
        category = payload.get("category")
        if category and category not in {c["id"] for c in connection["categories"]}:
            raise ServiceError("دسته‌بندی انتخاب‌شده در سایت وجود ندارد.")
        self.db.execute("UPDATE articles SET status='syncing',updated_at=? WHERE id=?", (now(), article["id"]))
        try:
            remote = await wp.sync(article, payload["status"], category, payload.get("date_gmt"))
        except Exception:
            self.db.execute("UPDATE articles SET status='uncertain',updated_at=? WHERE id=?", (now(), article["id"]))
            raise
        status = {"draft": "wp_draft", "publish": "published", "future": "scheduled"}.get(remote.get("status"))
        if not status or not isinstance(remote.get("id"), int):
            self.db.execute("UPDATE articles SET status='uncertain' WHERE id=?", (article["id"],))
            raise ServiceError("نتیجه وردپرس نیازمند بررسی است؛ ارسال دوباره خودکار انجام نشد.")
        self.db.execute("UPDATE articles SET status=?,wp_id=?,wp_url=?,synced_revision=?,remote_status=?,updated_at=? WHERE id=?",
                        (status, remote["id"], remote.get("link", ""), article["revision"], remote["status"], now(), article["id"]))
