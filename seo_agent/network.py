"""Public HTTPS requests, DNS pinning, bounded bodies and explicit redirects."""
import asyncio
import ipaddress
import json
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import USER_AGENT


class ServiceError(Exception):
    pass


def normalize_url(value):
    p = urlsplit(value.strip())
    try:
        port = p.port
    except ValueError:
        raise ServiceError("پورت آدرس معتبر نیست.")
    if p.scheme != "https" or not p.hostname or p.username or p.password or port not in (None, 443):
        raise ServiceError("یک آدرس عمومی HTTPS بدون رمز و با پورت استاندارد وارد کنید.")
    host = p.hostname.encode("idna").decode("ascii").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ServiceError("دسترسی به شبکه داخلی مجاز نیست.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ip.is_global:
            raise ServiceError("آدرس‌های خصوصی و داخلی مجاز نیستند.")
    host = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", host, p.path.rstrip("/") or "", p.query, ""))


def origin(url):
    p = urlsplit(url)
    return (p.scheme, p.hostname, p.port or 443)


class PublicHTTP:
    async def request(self, method, url, *, auth=None, payload=None, redirects=False, limit=2_000_000, allow_redirect=None):
        url = normalize_url(url)
        original_origin = origin(url)
        for _ in range(5):
            p = urlsplit(url)
            try:
                addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                    p.hostname, 443, type=socket.SOCK_STREAM), timeout=10)
            except (OSError, asyncio.TimeoutError) as e:
                raise ServiceError("دامنه قابل دسترسی نیست؛ آدرس و اتصال اینترنت را بررسی کنید.") from e
            ips = list(dict.fromkeys(a[4][0] for a in addresses))
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise ServiceError("مقصد درخواست به شبکه غیرعمومی اشاره می‌کند.")
            ip = ips[0]
            # Connect to the validated IP, but verify TLS against the original hostname.
            target = urlunsplit(("https", f"[{ip}]" if ":" in ip else ip, p.path or "/", p.query, ""))
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), trust_env=False) as client:
                    async with client.stream(method, target, headers={"Host": p.hostname, "User-Agent": USER_AGENT},
                                             auth=auth, json=payload,
                                             extensions={"sni_hostname": p.hostname}) as r:
                        body = bytearray()
                        async for chunk in r.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > limit:
                                raise ServiceError("پاسخ سایت از حد مجاز بزرگ‌تر است.")
                        result = httpx.Response(r.status_code, headers=r.headers, content=bytes(body),
                                                request=httpx.Request(method, url))
            except httpx.HTTPError as e:
                # Never include request headers, auth or response bodies in logs/errors.
                raise ServiceError("ارتباط امن با سایت ناموفق بود؛ درخواست خودکار تکرار نشد.") from e
            if result.is_redirect:
                if not redirects or method != "GET" or auth:
                    raise ServiceError("سایت درخواست را هدایت کرد؛ آدرس نهایی HTTPS را وارد کنید.")
                next_url = normalize_url(urljoin(url, result.headers.get("location", "")))
                if origin(next_url) != original_origin:
                    raise ServiceError("هدایت به دامنه دیگر مجاز نیست؛ آدرس نهایی سایت را وارد کنید.")
                if allow_redirect and not allow_redirect(next_url):
                    raise ServiceError("خواندن مقصد هدایت‌شده در robots.txt مجاز نیست.")
                url = next_url
                continue
            return result
        raise ServiceError("تعداد هدایت‌های سایت بیش از حد مجاز است.")


def json_response(response):
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError) as e:
        raise ServiceError("پاسخ API سایت JSON معتبر نیست.") from e
