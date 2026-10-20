"""BitPort search provider.

BitPort.io is primarily a cloud torrent client. This scraper targets the
public web search surfaces when available; prefer Jackett/Torznab when
configured via TORRENTSEARCH_TORZNAB.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
)
from ..network import NetworkClient
from ..provider import MagnetUriProvider, SearchProvider


class BitPort(SearchProvider, MagnetUriProvider):
    id = "bitport"
    name = "BitPort"
    url = "https://bitport.io"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True
    is_cloudflare_protected = True

    # Public mirrors / related search surfaces used historically with BitPort
    _SEARCH_URLS = (
        "https://bitport.io/search?q={q}",
        "https://www.bitport.io/search?q={q}",
    )

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = quote(query)
        last_err: Optional[Exception] = None
        for template in self._SEARCH_URLS:
            try:
                request_url = template.format(q=q)
                soup = await self.network.get_soup(request_url)
                out = self._parse(soup, category)
                if out:
                    return out
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        # Soft-fail with empty rather than hard error when the cloud UI has no
        # public index — callers can still use Torznab for private/Jackett feeds.
        if last_err and "403" in str(last_err):
            raise last_err
        return []

    def _parse(self, soup, category: Category) -> list[Torrent]:
        out: list[Torrent] = []
        for a in soup.select("a[href^='magnet:']"):
            magnet = a.get("href")
            if not magnet:
                continue
            row = a.find_parent("tr") or a.find_parent("li") or a.find_parent("div")
            name = H.text(a)
            details = None
            if row is not None:
                name_a = row.select_one("a[href]:not([href^='magnet:'])")
                if name_a is not None:
                    name = H.text(name_a) or name
                    href = name_a.get("href") or ""
                    if href and not href.startswith("magnet:"):
                        details = href if href.startswith("http") else f"{self.url}{href}"
            if not name:
                name = utils.get_info_hash_from_magnet_uri(magnet) or "torrent"
            info = utils.get_info_hash_from_magnet_uri(magnet) or name
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, info),
                    name=name,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetAvailable(value=magnet),
                    category=Category.OTHER,
                )
            )
        for a in soup.select("a[href*='/torrent'], a[href*='hash']"):
            href = a.get("href") or ""
            name = H.text(a)
            if not name or not href or href.startswith("magnet:"):
                continue
            details = href if href.startswith("http") else f"{self.url}{href}"
            rid = details.rstrip("/").rsplit("/", 1)[-1] or name
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetRequiresFetch(url=details),
                    category=Category.OTHER,
                )
            )
        return out

    async def get_magnet_uri(self, url: str) -> str:
        soup = await self.network.get_soup(url)
        a = soup.select_one("a[href^='magnet:']")
        if a is None or not a.get("href"):
            raise RuntimeError(f"No magnet on {url}")
        return a["href"]
