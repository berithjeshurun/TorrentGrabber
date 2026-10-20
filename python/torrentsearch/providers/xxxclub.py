"""XXXClub search provider — port of XXXClub.kt."""

from __future__ import annotations

from typing import Optional

from bs4 import Tag

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


class XXXClub(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "xxxclub"
    name = "XXXClub"
    url = "https://xxxclub.to"
    supported_categories = {Category.PORN}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _XXXClubResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/torrents/search/all/{query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _XXXClubDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/torrents/browse/all"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/torrents/top100"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _XXXClubDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _XXXClubResultsPageParser:
    LIST_ITEM_CONTAINER = "div.browsetableinside, div.divtableinside"
    LIST_ITEM = "ul > li"
    NAME = 'span:nth-child(2) > a[href^="/torrents/details"]'
    SIZE = "span.siz"
    SEEDERS = "span.see"
    PEERS = "span.lee"
    UPLOAD_DATE = "span.adde"
    DETAILS_PAGE_URL = NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        container = H.select_first(soup, self.LIST_ITEM_CONTAINER)
        if container is None:
            return []
        return [
            t
            for item in H.select(container, self.LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item: Tag, page_url: str) -> Optional[Torrent]:
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None
        torrent_id = utils.create_torrent_id(self._provider_id, details_page_url)
        name = H.text(H.select_first(list_item, self.NAME))
        if not name:
            return None
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _to_uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _to_uint(H.own_text(H.select_first(list_item, self.PEERS)))
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "dd MMM yyyy HH:mm:ss")
            if upload_raw
            else None
        )

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


class _XXXClubDetailsPageParser:
    NAME = "body > div > div.middle > div.main-content > div > h1"
    SIZE = "div.detailsdescr > ul > li:nth-child(2) > span:nth-child(3)"
    SEEDERS = "div.detailsdescr font.see"
    PEERS = "div.detailsdescr font.lee"
    UPLOAD_DATE = "div.detailsdescr > ul > li:nth-child(3) > span:nth-child(3)"
    UPLOADER = "div.detailsdescr > ul > li:nth-child(6) > span:nth-child(3)"
    LAST_CHECKED = "div.detailsdescr > ul > li:nth-child(5) > span:nth-child(3)"
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = (
        "div.detailsdescr > ul > li.downloadboxlist > span:nth-child(1) > a"
    )
    DESCRIPTION = "div.description"
    POSTER_URL = "img.detailsposter"
    DATE_FORMAT = "dd MMM yyyy HH:mm:ss"

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)
        name = H.own_text(H.select_first(soup, cls.NAME))
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)
        size = H.own_text(H.select_first(soup, cls.SIZE))
        seeders = _to_uint(H.own_text(H.select_first(soup, cls.SEEDERS)))
        peers = _to_uint(H.own_text(H.select_first(soup, cls.PEERS)))
        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, cls.DATE_FORMAT) if upload_raw else None
        )
        uploader = H.own_text(H.select_first(soup, cls.UPLOADER))
        last_checked_raw = H.own_text(H.select_first(soup, cls.LAST_CHECKED))
        last_checked = None
        if last_checked_raw is not None:
            # Some torrents will have "Pending" status.
            last_checked = utils.parse_date(last_checked_raw, cls.DATE_FORMAT)
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        description_el = H.select_first(soup, cls.DESCRIPTION)
        description = description_el.decode_contents() if description_el else None
        poster_url = H.attr(H.select_first(soup, cls.POSTER_URL), "src")

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
            description=description,
            poster_url=poster_url,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
