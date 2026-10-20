"""TorrentKitty search provider — port of TorrentKitty.kt."""

from __future__ import annotations

from typing import Optional

from bs4 import Tag

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
from ..provider import SearchProvider, TorrentDetailsProvider


class TorrentKitty(SearchProvider, TorrentDetailsProvider):
    id = "torrentkitty"
    name = "TorrentKitty"
    url = "https://torrentkitty.tv"
    supported_categories = {Category.OTHER}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _TorrentKittyResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search/{query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TorrentKittyDetailsPageParser.parse(response_html)


class _TorrentKittyResultsPageParser:
    LIST_ITEM = "table#archiveResult > tbody > tr"
    TORRENT_NAME = "td.name"
    SIZE = "td.size"
    UPLOAD_DATE = "td.date"
    MAGNET_URI = "td.action > a:nth-child(2)"
    FILE_DOWNLOAD_LINK = "td.action > a:nth-child(3)"
    DETAILS_PAGE_URL = "td.action > a:nth-child(1)"
    UPLOAD_DATE_FORMAT = "yyyy-MM-dd"

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        items = H.select(soup, self.LIST_ITEM)[1:]
        return [
            t
            for item in items
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item: Tag, page_url: str) -> Optional[Torrent]:
        torrent_name = H.own_text(H.select_first(list_item, self.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, self.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        torrent_id = utils.create_torrent_id(
            self._provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )
        size_raw = H.own_text(H.select_first(list_item, self.SIZE))
        size = size_raw.upper() if size_raw else None
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, self.UPLOAD_DATE_FORMAT)
            if upload_raw
            else None
        )
        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            provider_name=self._provider_name,
            details_page_url=details_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
        )


class _TorrentKittyDetailsPageParser:
    TORRENT_NAME = "h2"
    SIZE = "table.detailSummary > tbody > tr:nth-child(4) > td"
    UPLOAD_DATE = "table.detailSummary > tbody > tr:nth-child(5) > td"
    MAGNET_URI = "p.action > a:nth-child(2)"
    FILE_DOWNLOAD_LINK = "p.action > a:nth-child(1)"
    UPLOAD_DATE_FORMAT = "yyyy-MM-dd"

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)
        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        size_raw = H.own_text(H.select_first(soup, cls.SIZE))
        size = size_raw.upper() if size_raw else None
        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, cls.UPLOAD_DATE_FORMAT)
            if upload_raw
            else None
        )
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", ""
        )

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )
