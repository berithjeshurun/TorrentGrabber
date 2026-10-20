"""Torrends.to meta-search provider."""

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


class Torrends(SearchProvider, MagnetUriProvider):
    id = "torrends"
    name = "Torrends.to"
    url = "https://torrends.to"
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

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = quote(query)
        last_err: Optional[Exception] = None
        for path in (f"/search/{q}", f"/?q={q}", f"/search.php?q={q}", f"/search?q={q}"):
            try:
                request_url = f"{self.url}{path}"
                soup = await self.network.get_soup(request_url)
                out = self._parse(soup, category)
                if out:
                    return out
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        if last_err:
            raise last_err
        return []

    def _parse(self, soup, category: Category) -> list[Torrent]:
        out: list[Torrent] = []
        for a in soup.select("a[href^='magnet:']"):
            magnet = a.get("href")
            if not magnet:
                continue
            row = a.find_parent("tr") or a.find_parent("div") or a.parent
            name = None
            details = None
            if row is not None:
                name_a = row.select_one("a[href]:not([href^='magnet:'])")
                if name_a is not None:
                    name = H.text(name_a)
                    href = name_a.get("href") or ""
                    if href and not href.startswith("magnet:"):
                        details = href if href.startswith("http") else f"{self.url}{href}"
            if not name:
                name = H.text(a) or utils.get_info_hash_from_magnet_uri(magnet)
            if not name:
                continue
            size = seeders = peers = None
            if row is not None:
                for td in getattr(row, "find_all", lambda *_: [])("td"):
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

        # Fallback: detail links without inline magnets
        if not out:
            for a in soup.select("a[href*='/torrent'], a.result-title, a[href*='hash=']"):
                name = H.text(a)
                href = a.get("href") or ""
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


def _uint(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return None
