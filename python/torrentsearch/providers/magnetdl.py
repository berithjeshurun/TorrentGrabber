"""MagnetDL search provider."""

from __future__ import annotations

from typing import Optional
from urllib.parse import unquote

from .. import htmlutil as H
from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent
from ..network import NetworkClient
from ..provider import SearchProvider


class MagnetDL(SearchProvider):
    id = "magnetdl"
    name = "MagnetDL"
    url = "https://www.magnetdl.com"
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

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = unquote(query).strip().lower().replace(" ", "-")
        if not q:
            return []
        letter = q[0]
        url = f"{self.url}/{letter}/{q}/"
        soup = await self.network.get_soup(url)
        out: list[Torrent] = []
        for tr in soup.select("table.download tbody tr"):
            magnet_a = tr.select_one("a[href^='magnet:']")
            name_a = tr.select_one("td[class*='n'] a[href^='/file/'], td.n a, a[href*='/file/']")
            if magnet_a is None:
                continue
            magnet = magnet_a.get("href")
            if not magnet:
                continue
            name = H.text(name_a) if name_a is not None else H.text(tr)
            if not name:
                continue
            details = None
            if name_a is not None and name_a.get("href"):
                href = name_a["href"]
                details = href if href.startswith("http") else f"{self.url}{href}"
            tds = tr.find_all("td")
            # Typical columns vary; pick likely size/seed/leech from numeric-looking cells
            size = seeders = peers = None
            nums: list[str] = []
            for td in tds:
                t = H.text(td)
                if not t:
                    continue
                if any(u in t.lower() for u in ("kb", "mb", "gb", "tb")):
                    size = t
                elif t.replace(",", "").isdigit():
                    nums.append(t)
            if len(nums) >= 2:
                seeders = _uint(nums[-2])
                peers = _uint(nums[-1])
            elif len(nums) == 1:
                seeders = _uint(nums[0])
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
