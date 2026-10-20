"""HTML helpers bridging Jsoup semantics to BeautifulSoup."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

_PARSE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="html-parse")


def parse_html(html: str, page_url: str = "") -> BeautifulSoup:
    """
    Sync HTML parse.

    Prefer parse_html_async / NetworkClient.get_soup from async code so the
    event loop stays free while BeautifulSoup works.
    """
    return BeautifulSoup(html, "lxml")


async def parse_html_async(html: str, page_url: str = "") -> BeautifulSoup:
    """Parse off the event loop so searches stay responsive under load."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_PARSE_POOL, BeautifulSoup, html, "lxml")


async_parse_html = parse_html_async


def own_text(el: Optional[Tag]) -> Optional[str]:
    """Jsoup Element.ownText() — direct text nodes only."""
    if el is None:
        return None
    text = "".join(el.find_all(string=True, recursive=False)).strip()
    return text if text else None


def text(el: Optional[Tag]) -> Optional[str]:
    if el is None:
        return None
    t = el.get_text(strip=True)
    return t if t else None


def attr(el: Optional[Tag], name: str, base_url: str = "") -> Optional[str]:
    if el is None:
        return None
    if name.startswith("abs:"):
        raw = el.get(name[4:])
        if not raw:
            return None
        return urljoin(base_url, str(raw))
    raw = el.get(name)
    return str(raw) if raw is not None else None


def select(soup: BeautifulSoup | Tag, css: str) -> list[Tag]:
    return list(soup.select(css))


def select_first(soup: BeautifulSoup | Tag, css: str) -> Optional[Tag]:
    return soup.select_one(css)
