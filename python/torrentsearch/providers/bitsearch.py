"""BitSearch search provider — port of BitSearch.kt."""

from __future__ import annotations

import asyncio
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


def _category_from_raw_string(raw: str) -> Category:
    if raw in {
        "Other",
        "Other/Audio",
        "Other/Video",
        "Other/Image",
        "Other/Document",
        "Other/Program",
        "Other/Android",
        "Other/DiskImage",
        "Other/Source Code",
        "Other/Database",
        "Other/Archive",
    }:
        return Category.OTHER
    if raw in {"Movies", "Movies/Dub/Dual Audio"}:
        return Category.MOVIES
    if raw == "TV":
        return Category.SERIES
    if raw in {"Anime", "Anime/Dub/Dual Audio", "Anime/Subbed", "Anime/Raw"}:
        return Category.ANIME
    if raw in {
        "Softwares",
        "Softwares/Windows",
        "Softwares/Mac",
        "Softwares/Android",
    }:
        return Category.APPS
    if raw in {"Games", "Games/PC", "Games/Mac", "Games/Linux", "Games/Android"}:
        return Category.GAMES
    if raw in {"Music", "Music/mp3", "Music/Lossless", "Music/Album", "Music/Video"}:
        return Category.MUSIC
    if raw in {"AudioBook", "Ebook/Course"}:
        return Category.BOOKS
    if raw == "XXX":
        return Category.PORN
    return Category.OTHER


class BitSearch(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "bitsearch"
    name = "BitSearch"
    url = "https://bitsearch.to"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.PORN,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _BitSearchResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._get_category_id(category)
        pages = await asyncio.gather(
            *[
                self._search_page(query=query, category_id=category_id, page=page)
                for page in range(1, 6)
            ]
        )
        return [t for batch in pages for t in batch]

    async def _search_page(
        self, query: str, category_id: Optional[int], page: int
    ) -> list[Torrent]:
        request_url = f"{self.url}/search?q={query}&page={page}&sortBy=seeders"
        if category_id is not None:
            request_url += f"&category={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    def _get_category_id(self, category: Category) -> Optional[int]:
        return {
            Category.ALL: None,
            Category.ANIME: 4,
            Category.APPS: 5,
            Category.BOOKS: 9,
            Category.GAMES: 6,
            Category.MOVIES: 2,
            Category.MUSIC: 7,
            Category.PORN: 10,
            Category.SERIES: 3,
            Category.OTHER: 1,
        }.get(category)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _BitSearchDetailsPageParser.parse(
            html=response_html, page_url=details_page_url
        )

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/latest"
        cat_id = self._get_category_id(category)
        if cat_id is not None:
            request_url += f"?category={cat_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/trending"
        cat_id = self._get_category_id(category)
        if cat_id is not None:
            request_url += f"?category={cat_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)


class _BitSearchResultsPageParser:
    LIST_ITEM = "div.space-y-4 > div > div:nth-child(1)"
    TORRENT_INFO = "> div:nth-child(1)"
    DOWNLOAD_LINKS = "> div:nth-child(2)"
    CATEGORY_AND_METADATA = f"{TORRENT_INFO} > div:nth-last-child(2)"
    SWARM_STATS = f"{TORRENT_INFO} > div:nth-last-child(1)"
    TORRENT_NAME = f"{TORRENT_INFO} h3"
    SIZE = f"{CATEGORY_AND_METADATA} > span:nth-child(2) > span"
    SEEDERS = f"{SWARM_STATS} > span:nth-child(1) > span:nth-child(2)"
    PEERS = f"{SWARM_STATS} > span:nth-child(2) > span:nth-child(2)"
    UPLOAD_DATE = f"{CATEGORY_AND_METADATA} > span:nth-child(3) > span"
    CATEGORY = f"{CATEGORY_AND_METADATA} > span:nth-child(1) > span"
    MAGNET_LINK = f"{DOWNLOAD_LINKS} > a:nth-child(2)"
    FILE_DOWNLOAD_LINK = f"{DOWNLOAD_LINKS} > a:nth-child(1)"
    DETAILS_PAGE_URL = f"{TORRENT_NAME} > a"

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(soup, self.LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item: Tag, page_url: str) -> Optional[Torrent]:
        name_element = H.select_first(list_item, self.TORRENT_NAME)
        if name_element is None:
            return None
        name = H.text(name_element)
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, self.MAGNET_LINK), "href")
        if not magnet_uri:
            return None

        href = H.attr(name_element, "href") or ""
        torrent_remote_id = ""
        for ch in reversed(href):
            if ch == "/":
                break
            torrent_remote_id = ch + torrent_remote_id
        torrent_remote_id = torrent_remote_id if torrent_remote_id.strip() else None
        source_id = torrent_remote_id or utils.get_info_hash_from_magnet_uri(magnet_uri)
        torrent_id = utils.create_torrent_id(self._provider_id, source_id)

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders_raw = H.own_text(H.select_first(list_item, self.SEEDERS))
        peers_raw = H.own_text(H.select_first(list_item, self.PEERS))

        upload_date = None
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if upload_raw:
            upload_date = utils.parse_date(upload_raw, "M/d/yyyy")
            if upload_date is None:
                upload_date = utils.try_parse_relative(upload_raw)

        cat_raw = H.own_text(H.select_first(list_item, self.CATEGORY))
        category = _category_from_raw_string(cat_raw) if cat_raw else None
        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )

        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self._provider_name,
            upload_date=upload_date,
            category=category,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _BitSearchDetailsPageParser:
    NAME = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.bg-white.rounded-xl.shadow-lg.border.border-gray-200.overflow-hidden > "
        "div.bg-gradient-to-r.from-blue-600.to-purple-600.p-3.sm\\:p-4.text-white > "
        "div > div.flex.flex-col.sm\\:flex-row.gap-3.sm\\:gap-4 > "
        "div.flex-1.min-w-0.text-center.sm\\:text-left > h1"
    )
    SIZE = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.bg-white.rounded-xl.shadow-lg.border.border-gray-200.overflow-hidden > "
        "div.bg-gradient-to-r.from-blue-600.to-purple-600.p-3.sm\\:p-4.text-white > "
        "div > div.grid.grid-cols-2.sm\\:grid-cols-4.gap-2.sm\\:gap-3 > "
        "div:nth-child(3) > div.text-lg.sm\\:text-xl.font-bold.text-white"
    )
    SEEDERS = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.bg-white.rounded-xl.shadow-lg.border.border-gray-200.overflow-hidden > "
        "div.bg-gradient-to-r.from-blue-600.to-purple-600.p-3.sm\\:p-4.text-white > "
        "div > div.grid.grid-cols-2.sm\\:grid-cols-4.gap-2.sm\\:gap-3 > "
        "div:nth-child(1) > div.text-lg.sm\\:text-xl.font-bold.text-white"
    )
    PEERS = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.bg-white.rounded-xl.shadow-lg.border.border-gray-200.overflow-hidden > "
        "div.bg-gradient-to-r.from-blue-600.to-purple-600.p-3.sm\\:p-4.text-white > "
        "div > div.grid.grid-cols-2.sm\\:grid-cols-4.gap-2.sm\\:gap-3 > "
        "div:nth-child(2) > div.text-lg.sm\\:text-xl.font-bold.text-white"
    )
    UPLOAD_DATE = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.flex.flex-col.gap-6 > div:nth-child(1) > div.p-6 > "
        "div.grid.grid-cols-1.md\\:grid-cols-2.gap-8 > div.space-y-4 > div > "
        "div:nth-child(1) > div.text-sm.font-bold.text-gray-900"
    )
    LAST_CHECKED = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.flex.flex-col.gap-6 > div:nth-child(1) > div.p-6 > "
        "div.grid.grid-cols-1.md\\:grid-cols-2.gap-8 > div.space-y-4 > div > "
        "div:nth-child(2) > div.text-sm.font-bold.text-gray-900"
    )
    CATEGORY = (
        "body > div > main > div > main > div > "
        "div.lg\\:col-span-2.space-y-4.sm\\:space-y-6.lg\\:space-y-8 > "
        "div.bg-white.rounded-xl.shadow-lg.border.border-gray-200.overflow-hidden > "
        "div.bg-gradient-to-r.from-blue-600.to-purple-600.p-3.sm\\:p-4.text-white > "
        "div > div.flex.flex-col.sm\\:flex-row.gap-3.sm\\:gap-4 > "
        "div.flex-1.min-w-0.text-center.sm\\:text-left > "
        "div.flex.flex-wrap.justify-center.sm\\:justify-start.gap-1\\.5.sm\\:gap-2.mb-2.sm\\:mb-3 > "
        "span.inline-flex.items-center.px-2.sm\\:px-3.py-1.rounded-full.text-xs.font-semibold."
        "bg-white.bg-opacity-20.text-white.backdrop-blur-sm"
    )
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = 'a[href^="/download/torrent/"]'
    DATE_FORMAT = "M/d/yyyy"

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
        seeders_raw = H.own_text(H.select_first(soup, cls.SEEDERS))
        peers_raw = H.own_text(H.select_first(soup, cls.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, cls.DATE_FORMAT) if upload_raw else None
        )
        last_checked_raw = H.own_text(H.select_first(soup, cls.LAST_CHECKED))
        last_checked = (
            utils.parse_date(last_checked_raw, cls.DATE_FORMAT)
            if last_checked_raw
            else None
        )
        cat_raw = H.own_text(H.select_first(soup, cls.CATEGORY))
        category = _category_from_raw_string(cat_raw) if cat_raw else None
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
            category=category,
            last_checked=last_checked,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )
