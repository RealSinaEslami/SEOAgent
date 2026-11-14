import asyncio
import contextlib
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config
from .content import normalized, quality, sanitize
from .db import Database, now, uid, unpack
from .demo import seed_demo
from .network import PublicHTTP, ServiceError, normalize_url
from .security import Vault
from .wordpress import WordPress
from .worker import Worker


class Profile(BaseModel):
    business: str = Field(min_length=5, max_length=4000)
    audience: str = Field(default="", max_length=1500)
    language: str = Field(default="فارسی", max_length=50)
    tone: str = Field(default="حرفه‌ای و قابل فهم", max_length=200)
    market: str = Field(default="ایران", max_length=100)
    forbidden: str = Field(default="", max_length=2000)


class SiteInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    url: str = Field(max_length=1000)
    profile: Profile


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=10, max_length=200)


class SettingsInput(BaseModel):
    model: str = Field(min_length=2, max_length=100, pattern=r"^[a-zA-Z0-9._:/-]+$")
    api_key: str | None = Field(default=None, max_length=500)
    clear_key: bool = False


class TopicInput(BaseModel):
    title: str = Field(min_length=8, max_length=200)
    keyword: str = Field(min_length=2, max_length=120)
    intent: Literal["informational", "commercial", "transactional"] = "informational"


class GenerateInput(BaseModel):
    topic_id: str
    sources: list[str] = Field(default_factory=list, max_length=4)


class PipelineInput(BaseModel):
    count: int = Field(default=1, ge=1, le=5)
    upload_drafts: bool = False


class ArticleEdit(BaseModel):
    revision: int = Field(ge=1)
    title: str = Field(min_length=8, max_length=200)
    meta_description: str = Field(max_length=350)
    excerpt: str = Field(max_length=2000)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9-]+$")
    content_html: str = Field(min_length=30, max_length=150_000)


class ApprovalInput(BaseModel):
    revision: int
    confirmed: bool


class SyncInput(BaseModel):
    revision: int
    status: Literal["draft", "publish", "future"] = "draft"
    confirmed: bool = False
    category: int | None = Field(default=None, ge=1)
    scheduled_at: datetime | None = None


def create_app(data_dir=None, start_worker=True):
    directory = Path(data_dir or config.DATA_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    db = Database(directory / "seo.sqlite3")
    vault = Vault(directory)
    worker = Worker(db, vault, PublicHTTP())
    csrf = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        db.init()
        task = asyncio.create_task(worker.run()) if start_worker else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Roshd · SEO Agent", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db, app.state.worker, app.state.vault = db, worker, vault
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.url.path.startswith("/api") and request.method not in ("GET", "HEAD"):
            if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
                return JSONResponse({"detail": "نشست معتبر نیست؛ صفحه را تازه‌سازی کنید."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "مبدأ درخواست مجاز نیست."}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 250_000:
                return JSONResponse({"detail": "درخواست بیش از حد بزرگ است."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default output echoes invalid input, potentially including credentials.
        return JSONResponse({"detail": "مقادیر فرم معتبر نیستند؛ طول متن، آدرس و فیلدهای الزامی را بررسی کنید.",
                             "fields": [".".join(str(x) for x in e["loc"]) for e in error.errors()]}, status_code=422)

    def site_row(site_id):
        return worker.site(site_id)

    def article_row(article_id):
        article = db.one("SELECT * FROM articles WHERE id=?", (article_id,))
        if not article:
            raise HTTPException(404, "مقاله پیدا نشد.")
        return unpack(article, ["sources", "quality"])

    def idle(site_id):
        if db.one("SELECT id FROM jobs WHERE site_id=? AND status IN ('queued','running')", (site_id,)):
            raise ServiceError("پردازش سایت در حال اجراست؛ پس از پایان آن دوباره تلاش کنید.")

    def require_real(site):
        if site["demo"]:
            raise ServiceError("این یک پروژه نمایشی است؛ سایت واقعی خود را اضافه کنید.")

    def require_key():
        if not db.setting("openai_key") and not config.API_KEY:
            raise ServiceError("ابتدا کلید OpenAI را در تنظیمات وارد کنید.")

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"csrf_token": csrf, "version": "1.0.0", "settings": {
            "model": db.setting("model", config.MODEL), "has_api_key": bool(db.setting("openai_key") or config.API_KEY),
            "environment_key": bool(config.API_KEY)}}

    @app.put("/api/settings")
    def settings(data: SettingsInput):
        if db.one("SELECT id FROM jobs WHERE status IN ('queued','running')"):
            raise ServiceError("تا پایان پردازش جاری، تنظیمات مدل قابل تغییر نیست.")
        db.set_setting("model", data.model)
        if data.clear_key:
            db.execute("DELETE FROM settings WHERE key='openai_key'")
        elif data.api_key and data.api_key.strip():
            db.set_setting("openai_key", vault.encrypt(data.api_key.strip()))
        return {"ok": True}

    @app.get("/api/sites")
    def sites():
        rows = db.all("SELECT id,name,url,profile,analysis,connection,demo,created_at FROM sites ORDER BY created_at DESC")
        return [unpack(r, ["profile", "analysis", "connection"]) for r in rows]

    @app.post("/api/sites", status_code=201)
    def create_site(data: SiteInput):
        url = normalize_url(data.url)
        if urlsplit(url).query:
            raise ServiceError("آدرس ریشه سایت را بدون پارامتر وارد کنید.")
        site_id = uid()
        try:
            db.execute("INSERT INTO sites(id,name,url,profile,created_at) VALUES(?,?,?,?,?)",
                       (site_id, data.name.strip(), url, data.profile.model_dump_json(), now()))
        except sqlite3.IntegrityError as e:
            raise ServiceError("این سایت قبلاً ثبت شده است.") from e
        db.event(site_id, "سایت به فضای کار اضافه شد")
        return {"id": site_id}

    @app.put("/api/sites/{site_id}/profile")
    def edit_profile(site_id: str, data: Profile):
        site = site_row(site_id)
        require_real(site)
        idle(site_id)
        db.execute("UPDATE sites SET profile=? WHERE id=?", (data.model_dump_json(), site_id))
        db.event(site_id, "اطلاعات کسب‌وکار به‌روزرسانی شد")
        return {"ok": True}

    @app.post("/api/demo")
    def demo():
        return {"id": seed_demo(db)}

    @app.post("/api/sites/{site_id}/connect")
    async def connect(site_id: str, data: Credentials):
        site = site_row(site_id)
        require_real(site)
        idle(site_id)
        credentials = {"username": data.username.strip(), "password": data.password.strip()}
        wp = WordPress(worker.http, site["url"], **credentials)
        result = await wp.connect()
        db.execute("UPDATE sites SET credentials=?,connection=? WHERE id=?",
                   (vault.encrypt(json.dumps(credentials)), json.dumps(result, ensure_ascii=False), site_id))
        db.event(site_id, "اتصال وردپرس با موفقیت آزمایش شد")
        return result

    @app.delete("/api/sites/{site_id}/connection")
    def disconnect(site_id: str):
        site_row(site_id)
        idle(site_id)
        db.execute("UPDATE sites SET credentials=NULL,connection='{}' WHERE id=?", (site_id,))
        db.event(site_id, "دسترسی ذخیره‌شده وردپرس از برنامه حذف شد")
        return {"ok": True}

    @app.post("/api/sites/{site_id}/analyze")
    def analyze(site_id: str):
        require_real(site_row(site_id))
        require_key()
        return worker.enqueue(site_id, "analyze")

    @app.post("/api/sites/{site_id}/pipeline")
    def pipeline(site_id: str, data: PipelineInput):
        site = site_row(site_id)
        require_real(site)
        require_key()
        if data.upload_drafts and not site["connection"]:
            raise ServiceError("برای ارسال پیش‌نویس، ابتدا وردپرس را متصل کنید.")
        return worker.enqueue(site_id, "pipeline", data.model_dump())

    @app.get("/api/sites/{site_id}/topics")
    def topics(site_id: str):
        site_row(site_id)
        return [unpack(r, ["outline"]) for r in db.all("SELECT * FROM topics WHERE site_id=? ORDER BY rowid DESC LIMIT 200", (site_id,))]

    @app.post("/api/sites/{site_id}/topics", status_code=201)
    def create_topic(site_id: str, data: TopicInput):
        site_row(site_id)
        idle(site_id)
        topic_id = uid()
        try:
            db.execute("INSERT INTO topics(id,site_id,title,keyword,intent,rationale,outline) VALUES(?,?,?,?,?,?,?)",
                       (topic_id, site_id, data.title, normalized(data.keyword), data.intent, "موضوع انتخاب‌شده توسط شما", "[]"))
        except sqlite3.IntegrityError as e:
            raise ServiceError("این کلیدواژه قبلاً ثبت شده است.") from e
        return {"id": topic_id}

    @app.post("/api/sites/{site_id}/generate")
    def generate(site_id: str, data: GenerateInput):
        require_real(site_row(site_id))
        require_key()
        sources = [normalize_url(url) for url in data.sources]
        return worker.enqueue(site_id, "generate", {"topic_id": data.topic_id, "sources": sources})

    @app.get("/api/sites/{site_id}/articles")
    def articles(site_id: str):
        site_row(site_id)
        return [unpack(r, ["sources", "quality"]) for r in db.all("SELECT * FROM articles WHERE site_id=? ORDER BY created_at DESC LIMIT 200", (site_id,))]

    @app.get("/api/articles/{article_id}")
    def article(article_id: str):
        return article_row(article_id)

    @app.put("/api/articles/{article_id}")
    def edit_article(article_id: str, data: ArticleEdit):
        a = article_row(article_id)
        idle(a["site_id"])
        if a["status"] in ("syncing", "uncertain"):
            raise ServiceError("ابتدا وضعیت ارسال قبلی مقاله را بررسی کنید.")
        if a["revision"] != data.revision:
            raise HTTPException(409, "نسخه جدیدتری ذخیره شده است؛ مقاله را دوباره باز کنید.")
        updated = {**a, **data.model_dump(), "content_html": sanitize(data.content_html)}
        site = site_row(a["site_id"])
        titles = [p["title"] for p in site["analysis"].get("pages", [])]
        titles += [r["title"] for r in db.all("SELECT title FROM articles WHERE site_id=? AND id!=?", (a["site_id"], article_id))]
        report = quality(updated, titles)
        # Preserve editorial warnings from generation after manual edits.
        report["warnings"] = list(dict.fromkeys(report["warnings"] + a["quality"].get("warnings", [])))
        changed = db.execute("""UPDATE articles SET title=?,meta_description=?,excerpt=?,slug=?,content_html=?,quality=?,
                   revision=revision+1,approved_revision=NULL,status='draft',updated_at=? WHERE id=? AND revision=?""",
                   (data.title, data.meta_description, data.excerpt, data.slug, updated["content_html"],
                    json.dumps(report, ensure_ascii=False), now(), article_id, data.revision))
        if changed != 1:
            raise HTTPException(409, "مقاله هم‌زمان تغییر کرده است؛ نسخه جدید را باز کنید.")
        db.event(a["site_id"], "نسخه جدید مقاله ذخیره شد؛ تأیید نسخه قبلی بازنشانی شد")
        return article_row(article_id)

    @app.post("/api/articles/{article_id}/approve")
    def approve(article_id: str, data: ApprovalInput):
        a = article_row(article_id)
        idle(a["site_id"])
        if not data.confirmed or data.revision != a["revision"] or not a["quality"]["can_publish"]:
            raise ServiceError("صحت اطلاعات و نسخه فعلی را بررسی و خطاهای مسدودکننده را اصلاح کنید.")
        if a["status"] in ("uncertain", "syncing"):
            raise ServiceError("ابتدا وضعیت وردپرس را بررسی کنید.")
        changed = db.execute("UPDATE articles SET approved_revision=?,status='ready',updated_at=? WHERE id=? AND revision=?",
                             (data.revision, now(), article_id, data.revision))
        if changed != 1:
            raise HTTPException(409, "مقاله هم‌زمان تغییر کرده است؛ نسخه جدید را بازبینی کنید.")
        db.event(a["site_id"], "نسخه فعلی مقاله توسط کاربر تأیید شد")
        return article_row(article_id)

    @app.post("/api/articles/{article_id}/sync")
    def sync(article_id: str, data: SyncInput):
        a = article_row(article_id)
        site = site_row(a["site_id"])
        require_real(site)
        if not site["connection"]:
            raise ServiceError("ابتدا وردپرس را متصل کنید.")
        if a["revision"] != data.revision:
            raise HTTPException(409, "نسخه مقاله تغییر کرده است.")
        payload = {"article_id": article_id, "revision": data.revision, "status": data.status, "category": data.category}
        if data.status != "draft":
            if not data.confirmed or a["approved_revision"] != a["revision"] or not a["quality"]["can_publish"]:
                raise ServiceError("انتشار به تأیید صریح نسخه بازبینی‌شده نیاز دارد.")
        if data.status == "future":
            date = data.scheduled_at
            if not date or not date.tzinfo or date <= datetime.now(timezone.utc) + timedelta(minutes=2):
                raise ServiceError("زمان انتشار باید دارای منطقه زمانی و حداقل دو دقیقه در آینده باشد.")
            payload["date_gmt"] = date.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        return worker.enqueue(a["site_id"], "sync", payload)

    @app.get("/api/articles/{article_id}/export")
    def export(article_id: str):
        a = article_row(article_id)
        # Download includes metadata that WordPress core cannot expose to SEO plugins automatically.
        return Response(json.dumps(a, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{a["slug"]}.json"'})

    @app.get("/api/sites/{site_id}/activity")
    def activity(site_id: str):
        site_row(site_id)
        jobs = db.all("SELECT id,kind,status,progress,message,error,cancel_requested,created_at,updated_at FROM jobs WHERE site_id=? ORDER BY created_at DESC LIMIT 30", (site_id,))
        events = db.all("SELECT * FROM events WHERE site_id=? ORDER BY id DESC LIMIT 100", (site_id,))
        return {"jobs": jobs, "events": events}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "عملیات پیدا نشد.")
        if row["kind"] == "sync" and row["status"] == "running":
            raise ServiceError("ارسال به وردپرس آغاز شده؛ برای جلوگیری از نتیجه نامشخص قابل قطع نیست.")
        db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND status IN ('queued','running')", (job_id,))
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    static = config.ROOT / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
