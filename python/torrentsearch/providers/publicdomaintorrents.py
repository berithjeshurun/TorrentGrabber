"""Public Domain Torrents search provider."""

from __future__ import annotations

import re

from .. import htmlutil as H
from .. import utils
from ..models import Category, MagnetRequiresFetch, SearchProviderSafety, Torrent
from ..network import NetworkClient
from ..provider import SearchProvider


_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB|TB))", re.IGNORECASE)


class PublicDomainTorrents(SearchProvider):
    id = "publicdomaintorrents"
    name = "Public Domain Torrents"
    url = "https://www.publicdomaintorrents.info"
    supported_categories = {Category.MOVIES}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        # Site has no keyword search — load the full catalog and filter locally.
        catalog_url = f"{self.url}/nshowcat.html?category=ALL"
        soup = await self.network.get_soup(catalog_url)
        needles = [n for n in query.lower().split() if n]
        matches: list[Torrent] = []
        for a in soup.select("a[href*='nshowmovie.html']"):
            name = H.text(a)
            href = a.get("href") or ""
            if not name or not href:
                continue
            if needles and not all(n in name.lower() for n in needles):
                continue
            details = href if href.startswith("http") else f"{self.url}/{href.lstrip('/')}"
            movie_id = "unknown"
            if "movieid=" in details:
                movie_id = details.rsplit("movieid=", 1)[-1].split("&", 1)[0]
            matches.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, movie_id),
                    name=name,
                    category=Category.MOVIES,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetRequiresFetch(url=details),
                )
            )
            if len(matches) >= 100:
                break

        enriched: list[Torrent] = []
        for t in matches[:40]:
            try:
                enriched.append(await self._enrich(t))
            except Exception:  # noqa: BLE001
                enriched.append(t)
        return enriched or matches

    async def _enrich(self, torrent: Torrent) -> Torrent:
        if not torrent.details_page_url:
            return torrent
        soup = await self.network.get_soup(torrent.details_page_url)
        file_link = None
        size = None
        for a in soup.select("a[href*='btdownload.php']"):
            href = a.get("href") or ""
            if ".torrent" not in href:
                continue
            file_link = href
            label = H.text(a) or ""
            m = _SIZE_RE.search(label)
            if m:
                size = m.group(1)
            break
        if file_link is None:
            return torrent
        return Torrent(
            id=torrent.id,
            name=torrent.name,
            size=size or torrent.size,
            seeders=torrent.seeders,
            peers=torrent.peers,
            upload_date=torrent.upload_date,
            category=torrent.category,
            provider_name=torrent.provider_name,
            details_page_url=torrent.details_page_url,
            file_download_link=file_link,
            magnet_uri=torrent.magnet_uri,
        )
