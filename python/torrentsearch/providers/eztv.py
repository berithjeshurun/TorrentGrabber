"""Eztv search provider — port of Eztv.kt."""

from __future__ import annotations

import re
from typing import Optional

from bs4 import NavigableString

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


def _uint(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


class Eztv(SearchProvider, TorrentDetailsProvider):
    id = "eztvx"
    name = "EZTV"
    url = "https://eztvx.to"
    cloudflare_solver_url = f"{url}/home"
    supported_categories = {Category.SERIES}
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _EztvResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search/{query}"
        # Without setting that cookie, it returns results without magnet links.
        response_html = await self.network.get_text(
            url=request_url,
            headers={"Cookie": "layout=def_wlinks"},
        )
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _EztvDetailsPageParser.parse(response_html, details_page_url)


class _EztvResultsPageParser:
    LIST_ITEM = "table:last-of-type > tbody > tr"
    TORRENT_NAME = "td:nth-child(2) > a.epinfo"
    SIZE = "td:nth-child(4)"
    SEEDERS = "td:nth-child(6)"
    MAGNET_URI = "td:nth-child(3) > a.magnet"
    FILE_DOWNLOAD_LINK = "td:nth-child(3) > a:nth-child(2)"
    DETAILS_PAGE_URL = TORRENT_NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        results: list[Torrent] = []
        for item in H.select(soup, self.LIST_ITEM)[2:]:
            torrent = self._parse_list_item(item, page_url)
            if torrent is not None:
                results.append(torrent)
        return results

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        name_el = H.select_first(list_item, self.TORRENT_NAME)
        torrent_name = H.own_text(name_el)
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, self.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        torrent_id = utils.create_torrent_id(
            self._provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(name_el, "abs:href", page_url)

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=0,
            category=Category.SERIES,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _EztvDetailsPageParser:
    TORRENT_NAME = "#header_holder > table > tbody > tr:nth-child(1) > td > h1 > span"
    TORRENT_INFO_CONTAINER = (
        "#header_holder > table > tbody > tr:nth-child(2) > td > "
        "table.episode_columns_holder > tbody > tr:nth-child(1) > td:nth-child(3) > "
        "table > tbody > tr:nth-child(2) > td > table > tbody > tr:nth-child(1) > td"
    )
    SEEDERS = (
        "#header_holder > table > tbody > tr:nth-child(2) > td > "
        "table.episode_columns_holder > tbody > tr:nth-child(1) > td.episode_middle_column > "
        "table:nth-child(2) > tbody > tr:nth-child(2) > td > div > table > tbody > tr > "
        "td:nth-child(2) > span.stat_red"
    )
    PEERS = (
        "#header_holder > table > tbody > tr:nth-child(2) > td > "
        "table.episode_columns_holder > tbody > tr:nth-child(1) > td.episode_middle_column > "
        "table:nth-child(2) > tbody > tr:nth-child(2) > td > div > table > tbody > tr > "
        "td:nth-child(2) > span.stat_green"
    )
    MAGNET_URI = 'a[title="Magnet Link"]'
    FILE_DOWNLOAD_LINK = 'a[title="Download Torrent"]'
    DESCRIPTION = "div.desc_big"
    POSTER_URL = (
        "#header_holder > table > tbody > tr:nth-child(2) > td > "
        "table.episode_columns_holder > tbody > tr:nth-child(1) > td.episode_left_column > "
        "table > tbody > tr:nth-child(2) > td > a:nth-child(1) > img"
    )

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        seeders = _uint(H.own_text(H.select_first(soup, cls.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(soup, cls.PEERS)))

        torrent_info_container = H.select_first(soup, cls.TORRENT_INFO_CONTAINER)
        size = None
        upload_date = None
        if torrent_info_container is not None:
            for child in torrent_info_container.find_all(True, recursive=False):
                own = H.own_text(child) or ""
                if own == "Filesize:":
                    sib = child.next_sibling
                    if isinstance(sib, NavigableString):
                        size = str(sib).strip() or None
                elif own == "Released:":
                    sib = child.next_sibling
                    if isinstance(sib, NavigableString):
                        raw = str(sib).strip()
                        raw = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw)
                        upload_date = utils.parse_date(raw, "d MMM yyyy")

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
            category=Category.SERIES,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
        )
