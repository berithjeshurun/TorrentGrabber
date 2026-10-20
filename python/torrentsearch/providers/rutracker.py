"""RuTracker search provider (public mirror)."""

from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import quote, unquote

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


class RuTracker(SearchProvider, MagnetUriProvider):
    """Scrapes the public RuTracker.RU mirror (no login).

    Official rutracker.org requires credentials — wire that via Torznab/Jackett:
      TORRENTSEARCH_TORZNAB=RuTracker|http://127.0.0.1:9117/api/v2.0/indexers/rutracker/results/torznab|KEY
    """

    id = "rutracker"
    name = "RuTracker"
    url = "http://rutracker.ru"
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
        q = quote(unquote(query), safe="")
        # Official .org is a JS challenge. Mirrors answer; try them together.
        jobs = (
            ("http://rutracker.ru", False),
            ("https://rutracker.net", True),
        )

        async def one(base: str, forum: bool) -> list[Torrent]:
            if forum:
                url = f"{base}/forum/tracker.php?nm={q}&o=10&s=2&f[]=-1"
            else:
                url = (
                    f"{base}/tracker.php?nm={q}"
                    f"&o=10&s=2&f[]=-1&prev_allw=1&prev_a=0"
                    f"&prev_dla=0&prev_dlc=0&prev_dld=0&prev_dlw=0"
                    f"&prev_my=0&prev_new=0&prev_sd=0&prev_da=1&prev_dc=0"
                    f"&prev_df=1&prev_ds=0&prev_tor_type=0&dc=0&df=1&da=1"
                    f"&ds=0&tm=-1&sns=-1&srg=-1&allw=0"
                )
            soup = await self.network.get_soup(url)
            found = self._parse(soup, base)
            if found:
                self.url = base
            return found

        results = await asyncio.gather(
            *(one(base, forum) for base, forum in jobs),
            return_exceptions=True,
        )
        errors: list[Exception] = []
        for item in results:
            if isinstance(item, Exception):
                errors.append(item)
                continue
            if item:
                return item
        if errors and all(isinstance(item, Exception) for item in results):
            raise errors[0]
        return []

    def _parse(self, soup, base: str) -> list[Torrent]:
        out: list[Torrent] = []
        for tr in soup.select('tr[id^="tor_"], table#tor-tbl tr, #search-results tr'):
            name_a = tr.select_one(
                'a[href*="viewtopic.php"], a.tLink, a.med.tLink'
            )
            if name_a is None:
                continue
            name = H.text(name_a)
            href = name_a.get("href") or ""
            if not name or not href:
                continue
            if href.startswith("./"):
                href = href[1:]
            if href.startswith("/"):
                details = f"{base.rstrip('/')}{href}" if "forum" in base or True else f"{base}{href}"
                # Normalize forum vs root
                if "viewtopic.php" in href and "/forum/" not in details and "rutracker.org" in base:
                    details = f"{base}/forum/{href.lstrip('./')}"
                elif not href.startswith("http"):
                    details = href if href.startswith("http") else f"{base}/{href.lstrip('/')}"
            else:
                details = href if href.startswith("http") else f"{base}/{href.lstrip('/')}"

            magnet_a = tr.select_one("a[href^='magnet:']")
            if magnet_a is not None and magnet_a.get("href"):
                magnet_uri = MagnetAvailable(value=magnet_a["href"])
            else:
                magnet_uri = MagnetRequiresFetch(url=details)

            size = H.text(tr.select_one("td:nth-child(6), td.tor-size"))
            seeders = _uint(H.text(tr.select_one("td.seedmed b, td.seedmed, .seedmed")))
            peers = _uint(H.text(tr.select_one("td.leechmed b, td.leechmed, .leechmed")))
            rid = details
            if "t=" in details:
                rid = details.rsplit("t=", 1)[-1].split("&", 1)[0]
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    size=size or None,
                    seeders=seeders,
                    peers=peers,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=magnet_uri,
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


def _uint(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return None
