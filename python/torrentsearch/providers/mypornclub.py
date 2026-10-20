"""MyPornClub search provider — port of MyPornClub.kt."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
    TorrentDetails,
)
from ..network import NetworkClient
from ..provider import (
    LatestTorrentsProvider,
    MagnetUriProvider,
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


class MyPornClub(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "mypornclub"
    name = "MyPornClub"
    url = "https://myporn.club"
    supported_categories = {Category.PORN}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _MyPornClubResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        formatted_query = query.strip().replace("%20", "-")
        # TODO: Suffix can be used for sorting: /seeders, /latest, /hits, /views
        request_url = f"{self.url}/s/{formatted_query}/seeders"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _MyPornClubDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/ts/latest/alltime"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/ts/hits/alltime"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _MyPornClubDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _MyPornClubResultsPageParser:
    LIST_ITEM = "div.torrents_list > div.torrent_element"
    NAME = "div.torrent_element_text_div > a:nth-child(2) > span.torrent_element_text_span"
    SIZE = "div.torrent_element_info > span.teiv:nth-child(4)"
    SEEDERS = "div.torrent_element_info > span.teiv.teiv_seeders"
    PEERS = "div.torrent_element_info > span.teiv.teiv_leechers"
    UPLOAD_DATE = "div.torrent_element_info > span.teiv:nth-child(2)"
    DETAILS_PAGE_URL = "div.torrent_element_text_div > a:nth-child(2)"

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
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None

        torrent_remote_id = details_page_url.split("?", 1)[0].rsplit("/", 1)[-1]
        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        name = H.own_text(H.select_first(list_item, self.NAME))
        if not name:
            return None

        size_raw = H.own_text(H.select_first(list_item, self.SIZE))
        size = size_raw.upper() if size_raw else None
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(list_item, self.PEERS)))

        upload_date = None
        date_raw = H.text(H.select_first(list_item, self.UPLOAD_DATE))
        if date_raw:
            upload_date = utils.try_parse_relative(date_raw)

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self._provider_name,
            upload_date=upload_date,
            category=Category.PORN,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _MyPornClubDetailsPageParser:
    INFO_HASH = "div.torrent_info_div > div:nth-child(1)"
    NAME = "div.torrent_text"
    SIZE = "div.torrent_info_div span.tsize_span"
    SEEDERS = "div.torrent_info_div span.teiv_seeders"
    PEERS = "div.torrent_info_div span.teiv_leechers"
    UPLOAD_DATE = "div.torrent_info_div > div:nth-child(9)"
    UPLOADER = "div.torrent_info_div span.uploader_nick"
    LAST_CHECKED = "div.torrent_info_div > div:nth-child(8)"
    MAGNET_URI = "a.md_btn"
    FILE_DOWNLOAD_LINK = "a.td_btn"

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        info_hash_raw = H.own_text(H.select_first(soup, cls.INFO_HASH))
        if not info_hash_raw:
            return None
        info_hash = info_hash_raw.removeprefix("[hash_info]:").strip().lower()

        name_raw = H.text(H.select_first(soup, cls.NAME))
        if not name_raw:
            return None
        name = name_raw.split("#", 1)[0].strip()
        if not name:
            return None

        size_raw = H.own_text(H.select_first(soup, cls.SIZE))
        size = size_raw.upper() if size_raw else None
        seeders = _uint(H.own_text(H.select_first(soup, cls.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(soup, cls.PEERS)))

        upload_date = None
        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        if upload_raw:
            upload_date = utils.try_parse_relative(
                upload_raw.removeprefix("[uploaded]:").strip()
            )

        uploader_raw = H.own_text(H.select_first(soup, cls.UPLOADER))
        uploader = uploader_raw.removeprefix("@") if uploader_raw else None

        last_checked = None
        last_checked_raw = H.own_text(H.select_first(soup, cls.LAST_CHECKED))
        if last_checked_raw:
            last_checked = utils.try_parse_relative(
                last_checked_raw.removeprefix("[last checked]:").strip()
            )

        magnet_href = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        magnet_uri = magnet_href if magnet_href else utils.create_magnet_uri(info_hash)
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.PORN,
            uploader=uploader,
            last_checked=last_checked,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
