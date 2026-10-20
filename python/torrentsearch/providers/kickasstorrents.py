"""Kickass Torrents search provider."""

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


class KickassTorrents(SearchProvider, MagnetUriProvider):
    id = "kickasstorrents"
    name = "Kickass Torrents"
    url = "https://kickass.ws"
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
        "https://kickass.ws",
        "https://kickasstorrents.bz",
        "https://kkickass.com",
        "https://thekat.cc",
        "https://kickasstorrents.to",
    )

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = quote(query)
        last_err: Optional[Exception] = None
        for base in self._MIRRORS:
            for path in (f"/usearch/{q}/", f"/search/?q={q}", f"/search.php?q={q}"):
                try:
                    request_url = f"{base}{path}"
                    soup = await self.network.get_soup(request_url)
                    out = self._parse(soup, base, category)
                    if out:
                        self.url = base
                        return out
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    continue
        if last_err:
            raise last_err
        return []

    def _parse(self, soup, base: str, category: Category) -> list[Torrent]:
        out: list[Torrent] = []
        for tr in soup.select("table.data tr, table.table tr, table tr"):
            name_a = tr.select_one("a.cellMainLink, a[href*='/']")
            if name_a is None:
                continue
            name = H.text(name_a)
            href = name_a.get("href") or ""
            if not name or not href or href.startswith("magnet:"):
                continue
            # Skip nav rows
            if "cellMainLink" not in (name_a.get("class") or []) and not tr.select_one(
                "a[href^='magnet:'], a[data-download]"
            ):
                # still allow if magnet present in row
                if tr.select_one("a[href^='magnet:']") is None:
                    continue
            details = href if href.startswith("http") else f"{base}{href}"
            magnet_a = tr.select_one("a[href^='magnet:']")
            if magnet_a is not None and magnet_a.get("href"):
                magnet_uri = MagnetAvailable(value=magnet_a["href"])
            else:
                magnet_uri = MagnetRequiresFetch(url=details)
            tds = tr.find_all("td")
            size = seeders = peers = None
            nums: list[str] = []
            for td in tds:
                t = H.text(td)
                if not t:
                    continue
                if any(u in t.lower() for u in ("kb", "mb", "gb", "tb")) and size is None:
                    size = t
                elif t.replace(",", "").replace("N/A", "").strip().isdigit():
                    nums.append(t.replace(",", "").strip())
            if len(nums) >= 2:
                seeders = _uint(nums[-2])
                peers = _uint(nums[-1])
            elif len(nums) == 1:
                seeders = _uint(nums[0])
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
