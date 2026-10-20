"""iDope search provider."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
    SearchProviderSafety,
    Torrent,
)
from ..network import NetworkClient
from ..provider import SearchProvider


_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
)


class IDope(SearchProvider):
    id = "idope"
    name = "iDope"
    url = "https://idope.me"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
        Category.PORN,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True
    is_cloudflare_protected = True

    _MIRRORS = (
        "https://idope.me",
        "https://idope.se",
    )

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        path_q = quote(query.strip())
        last_err: Optional[Exception] = None
        for base in self._MIRRORS:
            try:
                request_url = f"{base}/torrent-list/{path_q}/"
                soup = await self.network.get_soup(request_url)
                out = self._parse(soup, base)
                self.url = base
                return out
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        if last_err:
            raise last_err
        return []

    def _parse(self, soup, base: str) -> list[Torrent]:
        out: list[Torrent] = []
        for row in soup.select("div.resultdiv"):
            name_el = row.select_one("div.resultdivtopname, div.resultdivtop a")
            name = H.text(name_el)
            if not name:
                continue
            details_a = row.select_one("div.resultdivtop a")
            details = None
            if details_a is not None and details_a.get("href"):
                href = details_a["href"]
                details = href if href.startswith("http") else f"{base}{href}"
            infohash = H.text(row.select_one("div.hideinfohash"))
            if not infohash:
                continue
            size = H.text(row.select_one("div.resultdivbottonlength, div.resultlength"))
            seeders = _uint(
                H.text(row.select_one("div.resultdivbottonseed, div.resultseed"))
            )
            ih = infohash.strip().upper()
            tr = "".join(f"&tr={quote(t, safe='')}" for t in _TRACKERS)
            magnet = f"magnet:?xt=urn:btih:{ih}&dn={quote(name)}{tr}"
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, ih),
                    name=name,
                    size=size or None,
                    seeders=seeders,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetAvailable(value=magnet),
                    category=Category.OTHER,
                )
            )
        return out


def _uint(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return None
