"""HTTP client mirrored from Android NetworkClient."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

import httpx

# Desktop Chrome profile — index pages expect a normal browser UA + Accept*.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    # Let httpx negotiate encoding; avoid forcing br if brotli isn't installed.
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
}

JSON_ACCEPT = "application/json, text/plain, */*"

MAX_RETRIES = 1
REQUEST_TIMEOUT = 20.0
CONNECT_TIMEOUT = 10.0

# These hosts reject Python's TLS fingerprint. Go straight to Chrome impersonation.
_IMPERSONATE_FIRST = {
    "torlock.com",
    "www.torlock.com",
    "torrentdownloads.pro",
    "rutracker.org",
    "rutracker.net",
    "rutracker.ru",
}


class CloudflareChallengeException(Exception):
    def __init__(self, url: str):
        super().__init__(f"Cloudflare challenge encountered [url={url}]")
        self.url = url


class NetworkClient:
    def __init__(self, timeout: float = REQUEST_TIMEOUT):
        self._timeout = httpx.Timeout(
            timeout,
            connect=CONNECT_TIMEOUT,
            read=timeout,
            write=timeout,
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._browser: Any = None

    async def __aenter__(self) -> NetworkClient:
        from .doh_transport import DohTransport

        self._client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=self._timeout,
            follow_redirects=True,
            http2=False,
            transport=DohTransport(),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("NetworkClient must be used as an async context manager")
        return self._client

    async def get(self, url: str, headers: Optional[dict[str, str]] = None) -> httpx.Response:
        response = await self._request_with_retry("GET", url, headers=headers)
        self._raise_if_challenged(response, url)
        return response

    async def get_text(self, url: str, headers: Optional[dict[str, str]] = None) -> str:
        return (await self.get(url, headers)).text

    async def get_soup(self, url: str, headers: Optional[dict[str, str]] = None):
        """Fetch HTML and parse off the event loop (non-blocking)."""
        from .htmlutil import parse_html_async

        text = await self.get_text(url, headers)
        return await parse_html_async(text, url)

    async def get_json(
        self, url: str, headers: Optional[dict[str, str]] = None
    ) -> Any | None:
        hdrs = {"Accept": JSON_ACCEPT, **(headers or {})}
        text = await self.get_text(url, hdrs)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def post_json(
        self,
        url: str,
        payload: Any | None = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any | None:
        hdrs = {"Accept": JSON_ACCEPT, **(headers or {})}
        if payload is not None:
            hdrs.setdefault("Content-Type", "application/json")
            content = json.dumps(payload)
        else:
            content = None
        response = await self._request_with_retry(
            "POST", url, headers=hdrs, content=content
        )
        self._raise_if_challenged(response, url)
        if not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return None

    async def submit_form(self, url: str, form_data: dict[str, str]) -> str:
        parsed = httpx.URL(url)
        origin = f"{parsed.scheme}://{parsed.host}"
        response = await self._request_with_retry(
            "POST",
            url,
            data=form_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": origin,
                "Referer": url,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        )
        self._raise_if_challenged(response, url)
        return response.text

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        content: Optional[str] = None,
        data: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        last_exc: Optional[BaseException] = None
        tried_browser = False

        async def browser_once() -> Optional[httpx.Response]:
            nonlocal tried_browser
            if tried_browser:
                return None
            tried_browser = True
            return await self._impersonated(
                method, url, headers=headers, content=content, data=data
            )

        host = (httpx.URL(url).host or "").lower()
        if host in _IMPERSONATE_FIRST:
            alt = await browser_once()
            if alt is not None and not self._looks_challenged(alt):
                return alt

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.request(
                    method, url, headers=headers, content=content, data=data
                )
                if response.status_code >= 500 and attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                if self._looks_challenged(response):
                    alt = await browser_once()
                    if alt is not None and not self._looks_challenged(alt):
                        return alt
                return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                alt = await browser_once()
                if alt is not None and not self._looks_challenged(alt):
                    return alt
                last_exc = exc
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _browser_fallback_enabled() -> bool:
        flag = (os.environ.get("TORRENTSEARCH_BROWSER") or "1").strip().lower()
        return flag not in ("0", "false", "off", "no")

    async def _impersonated(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        content: Optional[str] = None,
        data: Optional[dict[str, str]] = None,
    ) -> Optional[httpx.Response]:
        """Retry with curl_cffi Chrome impersonation (TLS fingerprint, not a browser)."""
        if not self._browser_fallback_enabled():
            return None
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            return None
        if self._browser is None:
            self._browser = AsyncSession(impersonate="chrome131")
        kwargs: dict[str, Any] = {
            "timeout": 25,
            "allow_redirects": True,
            "doh_url": "https://cloudflare-dns.com/dns-query",
            "headers": headers or None,
        }
        try:
            if method.upper() == "POST":
                raw = await self._browser.post(url, data=data or content, **kwargs)
            else:
                raw = await self._browser.request(method, url, **kwargs)
        except Exception:
            return None
        body = raw.content if isinstance(raw.content, (bytes, bytearray)) else bytes(raw.content or b"")
        return httpx.Response(
            status_code=raw.status_code,
            headers=dict(raw.headers),
            content=bytes(body),
            request=httpx.Request(method, str(raw.url or url)),
        )

    @staticmethod
    def _looks_challenged(response: httpx.Response) -> bool:
        if response.headers.get("cf-mitigated", "").lower() == "challenge":
            return True
        if response.status_code not in (403, 503):
            return False
        server = (response.headers.get("server") or "").lower()
        return "cloudflare" in server or bool(response.headers.get("cf-ray"))

    @staticmethod
    def _raise_if_challenged(response: httpx.Response, url: str) -> None:
        if response.headers.get("cf-mitigated", "").lower() == "challenge":
            raise CloudflareChallengeException(url)
        if response.status_code in (403, 503):
            # Mirror Android: treat 403/503 as CF challenge for protected sites.
            # Skip for APIs that legitimately return these (rare).
            server = (response.headers.get("server") or "").lower()
            if "cloudflare" in server or response.headers.get("cf-ray"):
                raise CloudflareChallengeException(url)
