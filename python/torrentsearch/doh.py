"""DNS-over-HTTPS so search ignores ISP sinkholes (plain port-53 rewrites)."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from typing import Optional

import httpx

# Bootstrap resolvers. These hosts are queried with normal DNS; torrent sites are not.
_DOH_URLS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)

_CACHE: dict[str, tuple[str, float]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_BOOTSTRAP: Optional[httpx.AsyncClient] = None


def doh_enabled() -> bool:
    flag = (os.environ.get("TORRENTSEARCH_DOH") or "1").strip().lower()
    return flag not in ("0", "false", "off", "no")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def _bootstrap() -> httpx.AsyncClient:
    global _BOOTSTRAP
    if _BOOTSTRAP is None:
        _BOOTSTRAP = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=5.0),
            follow_redirects=True,
            headers={"Accept": "application/dns-json"},
        )
    return _BOOTSTRAP


async def aclose_doh() -> None:
    global _BOOTSTRAP
    if _BOOTSTRAP is not None:
        await _BOOTSTRAP.aclose()
        _BOOTSTRAP = None


async def resolve(host: str) -> str:
    """Return an IPv4 address for host, or host itself if DoH is off / fails."""
    host = host.strip().rstrip(".").lower()
    if not host or _is_ip(host) or not doh_enabled():
        return host

    now = time.monotonic()
    cached = _CACHE.get(host)
    if cached and cached[1] > now:
        return cached[0]

    lock = _LOCKS.setdefault(host, asyncio.Lock())
    async with lock:
        cached = _CACHE.get(host)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]
        ip, ttl = await _lookup(host)
        if ip:
            _CACHE[host] = (ip, time.monotonic() + max(30, min(ttl, 3600)))
            return ip
        return host


async def _lookup(host: str) -> tuple[Optional[str], int]:
    client = await _bootstrap()
    for url in _DOH_URLS:
        try:
            response = await client.get(url, params={"name": host, "type": "A"})
            if response.status_code >= 400:
                continue
            data = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        answers = data.get("Answer") or []
        for ans in answers:
            if ans.get("type") != 1:
                continue
            ip = str(ans.get("data") or "")
            if _is_ip(ip):
                ttl = int(ans.get("TTL") or 300)
                return ip, ttl
    return None, 0
