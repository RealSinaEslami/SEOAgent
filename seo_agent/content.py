import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit

import bleach


ALLOWED_TAGS = ["p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "blockquote", "a", "br",
                "table", "thead", "tbody", "tr", "th", "td", "code", "pre"]


def sanitize(html, allowed_urls=None):
    def attributes(tag, name, value):
        return tag == "a" and (name == "title" or (name == "href" and (allowed_urls is None or unescape(value) in allowed_urls)))
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=attributes,
                        protocols=["https"], strip=True, strip_comments=True)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.links, self.headings = [], [], [], []
        self.skip = 0
        self.in_title = False
        self.heading = None
        self.description = ""
        self.noindex = False
        self.wordpress = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in ("h1", "h2", "h3"):
            self.heading = []
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "meta":
            if a.get("name", "").lower() == "description":
                self.description = a.get("content", "")
            if a.get("name", "").lower() == "robots" and "noindex" in a.get("content", "").lower():
                self.noindex = True
            if a.get("name", "").lower() == "generator" and "wordpress" in a.get("content", "").lower():
                self.wordpress = True
        if "wp-content" in a.get("href", "") or "wp-content" in a.get("src", ""):
            self.wordpress = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if tag in ("h1", "h2", "h3") and self.heading is not None:
            self.headings.append(" ".join(self.heading))
            self.heading = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title.append(data)
        if self.heading is not None:
            self.heading.append(data)
        self.parts.append(data)

    @property
    def text(self):
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def text_of(html):
    p = PageParser()
    p.feed(html)
    return p.text


def normalized(value):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).lower().translate(str.maketrans("يك", "یک"))).strip()


def quality(article, other_titles=()):
    html = article["content_html"]
    text = text_of(html)
    words = len(text.split())
    keyword = normalized(article["keyword"])
    title = normalized(article["title"])
    checks = [
        {"label": "عنوان مقاله مشخص است", "ok": 8 <= len(article["title"]) <= 160, "blocking": True},
        {"label": "مقاله حداقل ۳۰۰ کلمه دارد", "ok": words >= 300, "blocking": True},
        {"label": "ساختار بخش‌بندی دارد", "ok": len(re.findall(r"<h2\b", html)) >= 2, "blocking": False},
        {"label": "توضیحات متا بین ۷۰ تا ۱۸۰ نویسه است", "ok": 70 <= len(article["meta_description"]) <= 180, "blocking": False},
        {"label": "کلیدواژه در عنوان آمده است", "ok": bool(keyword) and keyword in title, "blocking": False},
        {"label": "عنوان یکسانی در سایت ثبت نشده", "ok": title not in {normalized(t) for t in other_titles}, "blocking": True},
        {"label": "متن جای‌نگهدار ندارد", "ok": not re.search(r"\[TODO\]|\[INSERT|لورم ایپسوم|lorem ipsum", text, re.I), "blocking": True},
        {"label": "منابع مقاله مشخص شده‌اند", "ok": bool(article["sources"]), "blocking": False},
    ]
    warnings = ["این بررسی‌ها ساختار متن را ارزیابی می‌کنند؛ صحت اطلاعات و رتبه گوگل را تأیید نمی‌کنند."]
    if any(w in text.lower() for w in ["درمان", "دارو", "سرمایه‌گذاری", "حقوقی", "diagnosis", "investment"]):
        warnings.append("موضوع حساس تشخیص داده شد؛ بازبینی متخصص لازم است.")
    return {"checks": checks, "warnings": warnings, "words": words,
            "score": round(100 * sum(x["ok"] for x in checks) / len(checks)),
            "can_publish": all(x["ok"] for x in checks if x["blocking"])}
