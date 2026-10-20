"""XXXTracker search provider — port of XXXTracker.kt."""

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
from ..provider import (
    LatestTorrentsProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

_MONTH_MAP = {
    "Янв": "Jan",
    "Фев": "Feb",
    "Мар": "Mar",
    "Апр": "Apr",
    "Май": "May",
    "Июн": "Jun",
    "Июл": "Jul",
    "Авг": "Aug",
    "Сен": "Sep",
    "Окт": "Oct",
    "Ноя": "Nov",
    "Дек": "Dec",
}


class XXXTracker(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "xxxtracker"
    name = "XXXTracker"
    url = "https://xxxtor.com"
    supported_categories = {Category.PORN}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _XXXTrackerResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/b.php?search={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _XXXTrackerDetailsPageParser.parse(
            html=response_html, page_url=details_page_url
        )

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/b.php"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    @staticmethod
    def normalize_upload_date(upload_date: str) -> str:
        # Russian month abbreviations from the provider are incompatible with
        # Java's locale data, so we normalize them to English before parsing.
        day, russian_month, year = upload_date.split(" ", 2)
        english_month = _MONTH_MAP[russian_month]
        return f"{day} {english_month} {year}"


class _XXXTrackerResultsPageParser:
    LIST_ITEM = "table > tbody > tr"
    TORRENT_NAME = "td:nth-child(2) > a:nth-child(3)"
    SIZE = "td:nth-last-child(2)"
    SEEDERS = "td:nth-last-child(1) > span.green"
    PEERS = "td:nth-last-child(1) > span.red"
    UPLOAD_DATE = "td:nth-child(1)"
    MAGNET_URI = "td:nth-child(2) > a:nth-child(1)"
    FILE_DOWNLOAD_LINK = "td:nth-child(2) > a:nth-child(2)"
    DETAILS_PAGE_URL = TORRENT_NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        # First item is used as a header.
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
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _to_uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _to_uint(H.own_text(H.select_first(list_item, self.PEERS)))
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        upload_date = None
        if upload_raw:
            normalized = XXXTracker.normalize_upload_date(upload_raw)
            upload_date = utils.parse_date(normalized, "dd MMM yy")
        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        torrent_id = utils.create_torrent_id(
            self._provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.PORN,
            details_page_url=details_page_url,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
        )


class _XXXTrackerDetailsPageParser:
    NAME = "#content > h1"
    SIZE = "#details > tbody > tr:nth-last-child(2) > td:nth-child(2)"
    SEEDERS = "#details > tbody > tr:nth-last-child(7) > td:nth-child(2)"
    PEERS = "#details > tbody > tr:nth-last-child(6) > td:nth-child(2)"
    UPLOAD_DATE = "#details > tbody > tr:nth-last-child(3) > td:nth-child(2)"
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = "#download > a:nth-child(1)"
    POSTER_URL = "#details > tbody > tr:nth-child(1) > td:nth-child(2) > img"
    DESCRIPTION = "#details > tbody > tr:nth-child(1) > td:nth-child(2)"

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)
        name = H.text(H.select_first(soup, cls.NAME))
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)

        size_raw = H.text(H.select_first(soup, cls.SIZE))
        size = None
        if size_raw:
            # takeWhile { it != '(' }.trim()
            size = size_raw.split("(", 1)[0].strip() or None

        seeders = _to_uint(H.text(H.select_first(soup, cls.SEEDERS)))
        peers = _to_uint(H.text(H.select_first(soup, cls.PEERS)))

        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = None
        if upload_raw:
            # takeWhile { !it.isWhitespace() }.trim()
            date_part = ""
            for ch in upload_raw:
                if ch.isspace():
                    break
                date_part += ch
            date_part = date_part.strip()
            if date_part:
                upload_date = utils.parse_date(date_part, "dd-MM-yyyy")

        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        poster_url = H.attr(H.select_first(soup, cls.POSTER_URL), "src")

        description = None
        description_el = H.select_first(soup, cls.DESCRIPTION)
        if description_el is not None:
            # Remove poster and two new lines after the poster from description.
            # Jsoup: select("> *:lt(3)").remove()
            children = [
                c for c in description_el.children if isinstance(c, Tag)
            ]
            for child in children[:3]:
                child.decompose()
            description = description_el.decode_contents()

        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.PORN,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
        )


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
