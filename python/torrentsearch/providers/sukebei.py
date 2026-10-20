"""Sukebei search provider — port of Sukebei.kt."""

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

LIST_ITEM = "table.torrent-list > tbody > tr"
NAME = "td:nth-child(2) > a:not(.comments)"
SIZE = "td:nth-child(4)"
SEEDERS = "td:nth-child(6)"
PEERS = "td:nth-child(7)"
UPLOAD_DATE = "td:nth-child(5)"
MAGNET_URI = "td:nth-child(3) > a:nth-child(2)"
FILE_DOWNLOAD_LINK = "td:nth-child(3) > a:nth-child(1)"
DETAILS_PAGE_URL = NAME

_TORRENT_INFO_CARD = "div.container > div.panel"
_TORRENT_INFO_CARD_BODY = f"{_TORRENT_INFO_CARD} > div.panel-body"
_D_TORRENT_NAME = f"{_TORRENT_INFO_CARD} > div.panel-heading > h3"
_D_SIZE = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(4) > div:nth-child(2)"
_D_SEEDERS = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(2) > div:nth-child(4)"
_D_PEERS = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(3) > div:nth-child(4)"
_D_UPLOAD_DATE = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(1) > div:nth-child(4)"
_D_UPLOADER = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(2) > div:nth-child(2)"
_D_DESCRIPTION = "#torrent-description"
_D_MAGNET_URI = 'a[href^="magnet:"]'
_D_FILE_DOWNLOAD_LINK = 'a[href^="/download"]'


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


class Sukebei(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "sukebeinyaa"
    name = "Sukebei"
    url = "https://sukebei.nyaa.si"
    supported_categories = {Category.PORN}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _SukebeiResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/?f=0&c=0_0&q={query}"
        response_html = await self.network.get_text(url=request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _SukebeiDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        response_html = await self.network.get_text(self.url)
        return self._results_parser.parse(response_html, self.url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/?s=seeders&o=desc"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)


class _SukebeiResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(dom, LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        torrent_name = H.own_text(H.select_first(list_item, NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, MAGNET_URI), "href")
        if not magnet_uri:
            return None
        file_download_link = H.attr(
            H.select_first(list_item, FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        size = H.own_text(H.select_first(list_item, SIZE))
        upload_date = None
        upload_el = H.select_first(list_item, UPLOAD_DATE)
        if upload_el is not None:
            ts = upload_el.get("data-timestamp")
            if ts is not None:
                try:
                    upload_date = utils.epoch_second_to_datetime(int(str(ts)))
                except ValueError:
                    pass
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))
        details_page_url = H.attr(
            H.select_first(list_item, DETAILS_PAGE_URL), "abs:href", page_url
        )

        torrent_remote_id = (
            details_page_url.rsplit("/", 1)[-1] if details_page_url else None
        )
        torrent_id = utils.create_torrent_id(
            self.provider_id,
            torrent_remote_id or utils.get_info_hash_from_magnet_uri(magnet_uri),
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self.provider_name,
            upload_date=upload_date,
            category=Category.PORN,
            magnet_uri=MagnetAvailable(magnet_uri),
            details_page_url=details_page_url,
            file_download_link=file_download_link,
        )


class _SukebeiDetailsPageParser:
    @staticmethod
    def parse(html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)
        size = H.own_text(H.select_first(dom, _D_SIZE))
        seeders = _to_int(H.text(H.select_first(dom, _D_SEEDERS)))
        peers = _to_int(H.text(H.select_first(dom, _D_PEERS)))
        upload_date = None
        upload_el = H.select_first(dom, _D_UPLOAD_DATE)
        if upload_el is not None:
            ts = upload_el.get("data-timestamp")
            if ts is not None:
                try:
                    upload_date = utils.epoch_second_to_datetime(int(str(ts)))
                except ValueError:
                    pass
        uploader = H.text(H.select_first(dom, _D_UPLOADER))
        file_download_link = H.attr(
            H.select_first(dom, _D_FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        description_el = H.select_first(dom, _D_DESCRIPTION)
        description = description_el.decode_contents() if description_el else None

        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.PORN,
            uploader=uploader,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
        )
