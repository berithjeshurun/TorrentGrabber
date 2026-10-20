"""LimeTorrents search provider — port of LimeTorrents.kt."""

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


def _category_from_raw_string(raw: str) -> Category:
    return {
        "TV": Category.SERIES,
        "Movie": Category.MOVIES,
        "Music": Category.MUSIC,
        "App": Category.APPS,
        "E-book": Category.BOOKS,
        "Anime": Category.ANIME,
        "Games": Category.GAMES,
    }.get(raw, Category.OTHER)


class LimeTorrents(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "limetorrents"
    name = "LimeTorrents"
    url = "https://limetorrents.fun"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.SERIES,
        Category.OTHER,
    }
    # Not technically unsafe, but it ignores category selection. Choose carefully.
    safety = SearchProviderSafety.UNSAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _LimeTorrentsResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_string = self._get_category_search_string(category)
        request_url = f"{self.url}/search/{category_string}/{query}/date/1/"
        response_html = await self.network.get_text(url=request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _LimeTorrentsDetailsPageParser.parse(response_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        if category not in self.supported_categories:
            return []
        category_string = self._get_category_browse_string(category)
        request_url = f"{self.url}/browse-torrents/{category_string}/"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(
            response_html, request_url, search_category=category
        )

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        if category not in self.supported_categories:
            return []
        category_string = self._get_category_browse_string(category)
        request_url = f"{self.url}/cat_top/16/{category_string}/"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(
            response_html, request_url, search_category=category
        )

    @staticmethod
    def _get_category_search_string(category: Category) -> str:
        return {
            Category.ALL: "all",
            Category.BOOKS: "all",
            Category.PORN: "all",
            Category.ANIME: "anime",
            Category.APPS: "applications",
            Category.GAMES: "games",
            Category.MOVIES: "movies",
            Category.MUSIC: "music",
            Category.SERIES: "tv",
            Category.OTHER: "other",
        }.get(category, "all")

    @staticmethod
    def _get_category_browse_string(category: Category) -> str:
        mapping = {
            Category.ANIME: "Anime",
            Category.APPS: "Applications",
            Category.BOOKS: "Other-E-books",
            Category.GAMES: "Games",
            Category.MOVIES: "Movies",
            Category.MUSIC: "Music",
            Category.SERIES: "TV-shows",
            Category.OTHER: "Other",
        }
        if category not in mapping:
            raise RuntimeError(f"Unsupported browse category: {category}")
        return mapping[category]


class _LimeTorrentsResultsPageParser:
    LIST_ITEM = ".table2 > tbody > tr"
    TORRENT_NAME = "td:nth-child(1) > div.tt-name > a:nth-child(2)"
    SIZE = "td:nth-child(3)"
    SEEDERS = "td.tdseed"
    PEERS = "td.tdleech"
    UPLOAD_DATE_AND_CATEGORY = "td:nth-child(2)"
    FILE_DOWNLOAD_LINK = "td:nth-child(1) > div.tt-name > a:nth-child(1)"
    DETAILS_PAGE_URL = TORRENT_NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(
        self,
        html: str,
        page_url: str,
        search_category: Optional[Category] = None,
    ) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        results: list[Torrent] = []
        for item in H.select(soup, self.LIST_ITEM):
            torrent = self._parse_list_item(item, page_url, search_category)
            if torrent is not None:
                results.append(torrent)
        return results

    def _parse_list_item(
        self,
        list_item,
        page_url: str,
        search_category: Optional[Category] = None,
    ) -> Optional[Torrent]:
        name_el = H.select_first(list_item, self.TORRENT_NAME)
        torrent_name = H.own_text(name_el)
        if not torrent_name:
            return None
        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "href"
        )
        if not file_download_link:
            return None

        # http://itorrents.net/torrent/7B6CAE55....torrent?title=...
        info_hash = (
            file_download_link.removeprefix("http://itorrents.net/torrent/")
            .split(".", 1)[0]
            .lower()
        )
        torrent_id = utils.create_torrent_id(self._provider_id, info_hash)
        magnet_uri = utils.create_magnet_uri(info_hash)
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(list_item, self.PEERS)))

        raw_upload_date = raw_category = None
        date_cat_text = H.own_text(
            H.select_first(list_item, self.UPLOAD_DATE_AND_CATEGORY)
        )
        if date_cat_text is not None:
            raw_upload_date, raw_category = self._parse_date_and_category(date_cat_text)

        upload_date = (
            utils.try_parse_relative(raw_upload_date) if raw_upload_date else None
        )
        category = (
            _category_from_raw_string(raw_category)
            if raw_category
            else search_category
        )
        details_page_url = H.attr(name_el, "abs:href", page_url)

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self._provider_name,
            upload_date=upload_date,
            category=category,
            details_page_url=details_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
        )

    @staticmethod
    def _parse_date_and_category(
        text: str,
    ) -> tuple[Optional[str], Optional[str]]:
        if "- in" not in text:
            return text.strip(), None
        parts = text.split("-", 1)
        raw_upload_date = parts[0].strip()
        raw_category = parts[1].strip().removeprefix("in ").removesuffix(".")
        return raw_upload_date, raw_category


class _LimeTorrentsDetailsPageParser:
    INFO_HASH = (
        "#content > div:nth-child(6) > div:nth-child(1) > div > table > "
        "tbody > tr:nth-child(1) > td:nth-child(2)"
    )
    NAME = "#content > h1"
    SIZE = (
        "#content > div:nth-child(6) > div:nth-child(1) > div > table > "
        "tbody > tr:nth-child(3) > td:nth-child(2)"
    )
    SEEDERS = "#content > span.greenish"
    PEERS = "#content > span.reddish"
    UPLOAD_DATE_AND_CATEGORY = (
        "#content > div:nth-child(6) > div:nth-child(1) > div > table > "
        "tbody > tr:nth-child(2) > td:nth-child(2)"
    )
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = (
        "#content > div:nth-child(6) > div:nth-child(1) > div > "
        "div:nth-child(7) > div > a"
    )

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)

        info_hash_raw = H.own_text(H.select_first(soup, cls.INFO_HASH))
        if not info_hash_raw:
            return None
        info_hash = info_hash_raw.lower()
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        name = H.own_text(H.select_first(soup, cls.NAME))
        if not name:
            return None
        size = H.own_text(H.select_first(soup, cls.SIZE))

        seeders_raw = H.own_text(H.select_first(soup, cls.SEEDERS))
        seeders = None
        if seeders_raw:
            seeders = _uint(seeders_raw.removeprefix("Seeders : ").strip())

        peers_raw = H.own_text(H.select_first(soup, cls.PEERS))
        peers = None
        if peers_raw:
            peers = _uint(peers_raw.removeprefix("Leechers : ").strip())

        raw_upload_date = raw_category = None
        date_cat = H.text(H.select_first(soup, cls.UPLOAD_DATE_AND_CATEGORY))
        if date_cat is not None:
            parts = date_cat.split("in", 1)
            raw_upload_date = parts[0].strip() if parts else None
            raw_category = parts[1].removesuffix(".") if len(parts) > 1 else None

        upload_date = (
            utils.try_parse_relative(raw_upload_date) if raw_upload_date else None
        )
        category = (
            _category_from_raw_string(raw_category) if raw_category else None
        )
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "href"
        )

        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )
