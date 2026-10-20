"""NekoBT search provider — port of NekoBT.kt."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
    SearchProviderSafety,
    Torrent,
    TorrentDetails,
)
from ..network import NetworkClient
from ..provider import (
    LatestTorrentsProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)


def _uint(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


class NekoBT(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
):
    # TODO: Use JSON API
    #       https://nekobt.to/search?query=one
    #       https://nekobt.to/api/v1/torrents/search?sort_by=seeders (top)
    #       https://nekobt.to/api/v1/torrents/search?sort_by=rss (latest)
    id = "nekobt"
    name = "NekoBT"
    url = "https://nekobt.to"
    supported_categories = {Category.ANIME}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _NekoBTResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?query={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?sort-by=latest"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?sort-by=seeders"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _NekoBTDetailsPageParser.parse(response_html, details_page_url)


class _NekoBTResultsPageParser:
    LIST_ITEM = "table.table > tbody > tr"
    TORRENT_NAME = "td:nth-child(3) > div:nth-child(1) > div > a"
    SIZE = "td:nth-child(5) > span"
    SEEDERS = "td:nth-child(7) > span"
    PEERS = "td:nth-child(8) > span"
    UPLOAD_DATE = "td:nth-child(6) > span"
    MAGNET_URI = "td:nth-child(4) > div > a:nth-child(1)"
    FILE_DOWNLOAD_LINK = "td:nth-child(4) > div > a:nth-child(2)"
    DETAILS_PAGE_URL = TORRENT_NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        results: list[Torrent] = []
        for item in H.select(soup, self.LIST_ITEM):
            torrent = self._parse_list_item(item, page_url)
            if torrent is not None:
                results.append(torrent)
        return results

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        name_el = H.select_first(list_item, self.TORRENT_NAME)
        if name_el is None:
            return None
        span_el = H.select_first(name_el, "span > span:nth-child(1)")
        torrent_name = H.own_text(span_el)
        if not torrent_name:
            return None

        magnet_uri = H.attr(H.select_first(list_item, self.MAGNET_URI), "href")
        if not magnet_uri:
            return None

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(list_item, self.PEERS)))

        upload_date = None
        date_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if date_raw:
            upload_date = utils.parse_date(date_raw, "yyyy-MM-dd HH:mm:ss")

        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(name_el, "abs:href", page_url)

        torrent_remote_id = (
            details_page_url.rsplit("/", 1)[-1] if details_page_url else None
        )
        torrent_id = utils.create_torrent_id(
            self._provider_id,
            torrent_remote_id or utils.get_info_hash_from_magnet_uri(magnet_uri),
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self._provider_name,
            upload_date=upload_date,
            category=Category.ANIME,
            details_page_url=details_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
        )


class _NekoBTDetailsPageParser:
    INFO_CARD = "div.grid > div:nth-child(1) > div.card:nth-child(1) > div.card-body"
    TORRENT_NAME = f"{INFO_CARD} > h2.card-title > div > span > span:nth-child(1)"
    SIZE = f'{INFO_CARD} > div:nth-child(2) > span[data-tip="Total Size"]'
    SEEDERS = f'{INFO_CARD} > div:nth-child(2) > span[data-tip="Seeders"]'
    PEERS = f'{INFO_CARD} > div:nth-child(2) > span[data-tip="Leechers"]'
    UPLOAD_DATE = f"{INFO_CARD} > div:nth-child(2) span:nth-child(8)"
    UPLOADER = f'{INFO_CARD} > div:nth-child(2) > span[data-tip="Uploader"] > a'
    MAGNET_URI = f'{INFO_CARD} a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = f'{INFO_CARD} a[href^="/api/v1/torrents"]'
    DESCRIPTION = "div.grid > div:nth-child(1) > div.card:nth-last-child(3) div.markdown"
    POSTER_URL = 'img[alt^="Banner for "]'

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None

        size = H.own_text(H.select_first(soup, cls.SIZE))
        seeders = _uint(H.own_text(H.select_first(soup, cls.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(soup, cls.PEERS)))

        upload_date = None
        date_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        if date_raw:
            upload_date = utils.parse_date(date_raw, "yyyy-MM-dd HH:mm:ss")

        uploader = H.own_text(H.select_first(soup, cls.UPLOADER))
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        desc_el = H.select_first(soup, cls.DESCRIPTION)
        description = desc_el.decode_contents() if desc_el is not None else None
        poster_url = H.attr(H.select_first(soup, cls.POSTER_URL), "abs:src", page_url)

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.ANIME,
            uploader=uploader,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
        )
