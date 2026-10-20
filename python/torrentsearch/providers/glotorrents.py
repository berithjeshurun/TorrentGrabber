"""GloTorrents (glodls) search provider."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent
from ..network import NetworkClient
from ..provider import SearchProvider


class GloTorrents(SearchProvider):
    id = "glotorrents"
    name = "GloTorrents"
    url = "https://glodls.to"
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

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        url = (
            f"{self.url}/search_results.php?search={query}"
            f"&cat=0&incldead=0&inclexternal=0&lang=0&sort=seeders&order=desc"
        )
        soup = await self.network.get_soup(url)
        out: list[Torrent] = []
        for tr in soup.select("table.ttable_headinner tr, table tr"):
            magnet_a = tr.select_one("a[href^='magnet:']")
            name_a = tr.select_one("a[href*='details.php'], a.ttable_header")
            if magnet_a is None:
                continue
            magnet = magnet_a.get("href")
            if not magnet:
                continue
            name = H.text(name_a) if name_a is not None else None
            if not name:
                # sometimes name is previous link
                for a in tr.select("a"):
                    href = a.get("href") or ""
                    if "details.php" in href or "torrent-" in href:
                        name = H.text(a)
                        name_a = a
                        break
            if not name:
                continue
            details = None
            if name_a is not None and name_a.get("href"):
                href = name_a["href"]
                details = href if href.startswith("http") else f"{self.url}/{href.lstrip('/')}"
            tds = tr.find_all("td")
            size = seeders = peers = None
            for td in tds:
                t = H.text(td)
                if any(u in t.lower() for u in ("kb", "mb", "gb", "tb")) and size is None:
                    size = t
                elif t.replace(",", "").isdigit():
                    if seeders is None:
                        seeders = _uint(t)
                    elif peers is None:
                        peers = _uint(t)
            info = utils.get_info_hash_from_magnet_uri(magnet) or name
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, info),
                    name=name,
                    size=size,
                    seeders=seeders,
                    peers=peers,
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
