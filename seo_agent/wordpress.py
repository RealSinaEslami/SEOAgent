import httpx

from .network import ServiceError, json_response


class WordPress:
    def __init__(self, http, url, username, password):
        self.http = http
        self.base = url.rstrip("/") + "/wp-json/wp/v2"
        self.auth = httpx.BasicAuth(username, password)

    async def request(self, method, path, payload=None):
        r = await self.http.request(method, self.base + path, auth=self.auth, payload=payload)
        if r.status_code in (401, 403):
            raise ServiceError("وردپرس دسترسی را رد کرد؛ Application Password و نقش کاربر را بررسی کنید.")
        if not r.is_success:
            raise ServiceError(f"وردپرس عملیات را نپذیرفت (HTTP {r.status_code}). تنظیمات سایت را بررسی کنید.")
        return json_response(r)

    async def connect(self):
        user = await self.request("GET", "/users/me?context=edit")
        caps = user.get("capabilities", {})
        if not caps.get("edit_posts"):
            raise ServiceError("این کاربر اجازه ساخت نوشته ندارد.")
        categories = await self.request("GET", "/categories?per_page=100")
        return {"user": user.get("name", ""), "can_publish": bool(caps.get("publish_posts")),
                "can_upload": bool(caps.get("upload_files")),
                "categories": [{"id": c["id"], "name": c["name"]} for c in categories]}

    async def find(self, slug):
        from urllib.parse import quote
        posts = await self.request("GET", "/posts?context=edit&status=draft,pending,publish,future,private&slug=" + quote(slug))
        if not isinstance(posts, list):
            raise ServiceError("فهرست نوشته‌های وردپرس معتبر نیست.")
        return posts[0] if posts else None

    async def sync(self, article, status, category=None, date_gmt=None):
        # Deterministic unique slug allows recovery after an ambiguous network timeout.
        slug = "seoagent-" + article["id"]
        remote = None
        if article.get("wp_id"):
            remote = await self.request("GET", f"/posts/{article['wp_id']}?context=edit")
        else:
            remote = await self.find(slug)
        if remote and remote.get("status") in ("publish", "future") and status == "draft":
            raise ServiceError("این نوشته در وردپرس منتشر یا زمان‌بندی شده است؛ تبدیل خودکار به پیش‌نویس مجاز نیست.")
        payload = {"title": article["title"], "content": article["content_html"],
                   "excerpt": article["excerpt"], "status": status}
        # Stable slug remains unique across retries. Editorial SEO slug is available for export.
        if not remote:
            payload["slug"] = slug
        if category:
            payload["categories"] = [category]
        if date_gmt:
            payload["date_gmt"] = date_gmt
        if remote:
            return await self.request("POST", f"/posts/{remote['id']}", payload)
        return await self.request("POST", "/posts", payload)
