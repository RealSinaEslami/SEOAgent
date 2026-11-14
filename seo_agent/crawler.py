import asyncio
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from .config import USER_AGENT
from .content import PageParser
from .network import ServiceError, normalize_url, origin


class Crawler:
    def __init__(self, http):
        self.http = http

    async def robots(self, url):
        root = urlunsplit((*urlsplit(url)[:2], "/robots.txt", "", ""))
        r = await self.http.request("GET", root, redirects=True, limit=150_000)
        if r.status_code not in (200, 404):
            raise ServiceError("دریافت robots.txt ناموفق بود؛ برای رعایت محدودیت سایت، تحلیل متوقف شد.")
        robot = RobotFileParser()
        robot.parse(r.text.splitlines() if r.status_code == 200 else [])
        return robot

    async def page(self, url, robot=None):
        robot = robot or await self.robots(url)
        if not robot.can_fetch(USER_AGENT, url):
            raise ServiceError("خواندن این صفحه در robots.txt مجاز نیست.")
        r = await self.http.request("GET", url, redirects=True,
                                    allow_redirect=lambda target: robot.can_fetch(USER_AGENT, target))
        if not r.is_success or "text/html" not in r.headers.get("content-type", ""):
            raise ServiceError("صفحه HTML قابل خواندن نیست.")
        parser = PageParser()
        parser.feed(r.text)
        return {"url": str(r.url), "title": " ".join(parser.title)[:300], "description": parser.description[:500],
                "text": parser.text[:9000], "headings": parser.headings[:30], "links": parser.links[:300],
                "noindex": parser.noindex or "noindex" in r.headers.get("x-robots-tag", "").lower(),
                "wordpress": parser.wordpress}

    async def scan(self, url, progress):
        robot = await self.robots(url)
        homepage = await self.page(url, robot)
        root = url.rstrip("/") + "/"
        candidates = list(homepage.pop("links"))
        sitemap_found = False
        warnings = []
        sitemap_queue = [urljoin(root, "sitemap.xml")] + list(robot.site_maps() or [])
        visited_maps = set()
        for _ in range(4):
            if not sitemap_queue:
                break
            map_url = sitemap_queue.pop(0)
            if map_url in visited_maps or origin(map_url) != origin(url):
                continue
            visited_maps.add(map_url)
            try:
                if not robot.can_fetch(USER_AGENT, map_url):
                    continue
                r = await self.http.request("GET", map_url, redirects=True, limit=1_000_000)
                if r.status_code == 200:
                    xml = ElementTree.fromstring(r.content)
                    locs = [e.text for e in xml.iter() if e.tag.split("}")[-1] == "loc" and e.text][:200]
                    if xml.tag.split("}")[-1] == "sitemapindex":
                        sitemap_queue.extend(locs)
                    else:
                        candidates.extend(locs)
                    sitemap_found = True
            except (ServiceError, ElementTree.ParseError):
                warnings.append("یکی از نقشه‌های سایت قابل خواندن نبود.")
        pages, seen = [homepage], {normalize_url(url)}
        rate = robot.request_rate(USER_AGENT) or robot.request_rate("*")
        delay = max(robot.crawl_delay(USER_AGENT) or robot.crawl_delay("*") or 1,
                    rate.seconds / rate.requests if rate and rate.requests else 1)
        if delay > 60:
            raise ServiceError("فاصله درخواست مجاز سایت بیش از یک دقیقه است؛ تحلیل خودکار این سایت پشتیبانی نمی‌شود.")
        attempts = 0
        for link in candidates[:350]:
            if len(pages) >= 12 or attempts >= 24:
                break
            try:
                target = normalize_url(urljoin(root, link))
                p = urlsplit(target)
                if origin(target) != origin(url) or target in seen or p.query:
                    continue
                if any(s in p.path.lower() for s in ["wp-admin", "wp-login", "/cart", "/checkout", "/feed"]):
                    continue
                if p.path.lower().endswith((".jpg", ".png", ".pdf", ".zip", ".xml")):
                    continue
                seen.add(target)
                if not robot.can_fetch(USER_AGENT, target):
                    continue
                await progress(10 + len(pages) * 4, f"خواندن صفحه {len(pages) + 1} از حداکثر ۱۲ صفحه")
                attempts += 1
                await asyncio.sleep(delay)
                page = await self.page(target, robot)
                page.pop("links", None)
                pages.append(page)
            except ServiceError:
                warnings.append("یک صفحه داخلی در دسترس نبود یا خواندن آن مجاز نبود.")
        if len(pages) < 2:
            warnings.append("صفحات کمی خوانده شد؛ سایت‌های JavaScript یا محدودشده به اطلاعات تکمیلی نیاز دارند.")
        return {"pages": pages, "cms": "WordPress" if homepage["wordpress"] else "نامشخص",
                "sitemap_found": sitemap_found, "warnings": list(dict.fromkeys(warnings))}
