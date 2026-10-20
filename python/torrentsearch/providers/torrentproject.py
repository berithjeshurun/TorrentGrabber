"""TorrentProject search provider."""

from __future__ import annotations

from typing import Optional
from urllib.parse import unquote

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
)
from ..network import NetworkClient
from ..provider import MagnetUriProvider, SearchProvider


class TorrentProject(SearchProvider, MagnetUriProvider):
    id = "torrentproject"
    name = "TorrentProject"
    url = "https://torrentproject.com.se"
    supported_categories = {Category.OTHER}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        _ = unquote(query)
        url = f"{self.url}/browse?t={query}&p=0"
        soup = await self.network.get_soup(url)
        out: list[Torrent] = []
        root = soup.select_one("#similarfiles")
        if root is None:
            return []
        for div in root.find_all("div", recursive=False):
            classes = " ".join(div.get("class") or [])
            if "gac_bb" in classes:
                continue
            spans = div.find_all("span")
            if len(spans) < 6:
                continue
            a = spans[0].find("a") if spans else None
            if a is None:
                continue
            name = H.text(a)
            href = a.get("href") or ""
            if not name or name.lower() == "nome":
                continue
            details = href if href.startswith("http") else f"{self.url}{href}"
            seeders = _uint(H.text(spans[2]))
            peers = _uint(H.text(spans[3]))
            size = H.text(spans[5]) or None
            rid = details.rstrip("/").rsplit("/", 1)[-1] or name
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    size=size,
                    seeders=seeders,
                    peers=peers,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetRequiresFetch(url=details),
                    category=Category.OTHER,
                )
            )
        return out

    async def get_magnet_uri(self, url: str) -> str:
        html = await self.network.get_text(url)
        import re
        from urllib.parse import unquote as uq

        m = re.search(r"""href=['"](magnet:[^'"]+)['"]""", html, re.I)
        if not m:
            raise RuntimeError(f"No magnet on {url}")
        return uq(m.group(1))


def _uint(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return None
