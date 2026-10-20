"""Dmhy search provider — port of Dmhy.kt."""

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


def _get_category_from_id(cat_id: str) -> Category:
    if cat_id in {"2", "7", "31"}:
        return Category.ANIME
    if cat_id == "3":
        return Category.BOOKS
    if cat_id in {"41", "42"}:
        return Category.SERIES
    if cat_id in {"4", "43", "44", "15"}:
        return Category.MUSIC
    if cat_id == "6":
        return Category.SERIES
    if cat_id in {"9", "17", "18", "19", "20", "21"}:
        return Category.GAMES
    return Category.OTHER


class Dmhy(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "dmhy"
    name = "Dmhy"
    url = "https://share.dmhy.org"
    supported_categories = {
        Category.ANIME,
        Category.BOOKS,
        Category.GAMES,
        Category.MUSIC,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _DmhyResultsPageParser(self.id, self.name)
        self._category_map = {
            Category.ALL: 0,
            Category.ANIME: 2,
            Category.BOOKS: 3,
            Category.GAMES: 9,
            Category.MUSIC: 4,
            Category.SERIES: 6,
            Category.OTHER: 1,
        }

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._category_map.get(
            category, self._category_map[Category.ALL]
        )
        request_url = (
            f"{self.url}/topics/list"
            f"?keyword={query}"
            f"&sort_id={category_id}"
            f"&team_id=0"
            f"&order=date-desc"
        )
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _DmhyDetailsPageParser.parse(response_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        category_id = self._category_map.get(
            category, self._category_map[Category.ALL]
        )
        request_url = f"{self.url}/topics/list/sort_id/{category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        return await self.get_latest_torrents(category)


class _DmhyResultsPageParser:
    LIST_ITEM = "table#topic_list > tbody > tr"
    TORRENT_NAME = "td.title > a"
    SIZE = "td:nth-child(5)"
    SEEDERS = "td:nth-child(6)"
    PEERS = "td:nth-child(7)"
    UPLOAD_DATE = "td:nth-child(1) > span"
    CATEGORY = "td:nth-child(2) > a"
    MAGNET_URI = 'td:nth-child(4) > a[href^="magnet:?xt="]'
    DETAILS_PAGE_URL = TORRENT_NAME

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
        size = utils.normalize_size(size_raw) if size_raw else None

        seeders_raw = H.text(H.select_first(list_item, self.SEEDERS))
        peers_raw = H.text(H.select_first(list_item, self.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_date = None
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if upload_raw:
            upload_date = utils.parse_date(upload_raw, "yyyy/MM/dd HH:mm")

        category = None
        cat_el = H.select_first(list_item, self.CATEGORY)
        if cat_el is not None:
            class_name = " ".join(cat_el.get("class") or [])
            if class_name.startswith("sort-"):
                category = _get_category_from_id(class_name.removeprefix("sort-"))

        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            details_page_url=details_page_url,
        )


class _DmhyDetailsPageParser:
    TORRENT_NAME = "div.topic-title > h3"
    SIZE = "div.topic-title > div.info > ul > li:nth-child(6) > span"
    UPLOAD_DATE = "div.topic-title > div.info > ul > li:nth-child(2) > span"
    CATEGORY = "div.topic-title > div.info > ul > li:nth-child(1) > span > a"
    DESCRIPTION = "div.topic-nfo"
    MAGNET_URI = "div#resource-tabs > div#tabs-1 > p:nth-child(2) > a#a_magnet"

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)

        torrent_name = H.text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None

        size_raw = H.text(H.select_first(soup, cls.SIZE))
        size = utils.normalize_size(size_raw) if size_raw else None

        upload_date = None
        upload_raw = H.text(H.select_first(soup, cls.UPLOAD_DATE))
        if upload_raw:
            date_part = ""
            for ch in upload_raw:
                if ch.isspace():
                    break
                date_part += ch
            if date_part:
                upload_date = utils.parse_date(date_part, "yyyy/MM/dd")

        category = None
        cat_href = H.attr(H.select_first(soup, cls.CATEGORY), "href")
        if cat_href:
            prefix = "/topics/list/sort_id/"
            if cat_href.startswith(prefix):
                category = _get_category_from_id(cat_href[len(prefix) :])

        description = None
        desc_el = H.select_first(soup, cls.DESCRIPTION)
        if desc_el is not None:
            # Jsoup: select("> *:lt(2)").remove()
            direct_children = list(desc_el.find_all(True, recursive=False))
            for child in direct_children[:2]:
                child.decompose()
            description = desc_el.decode_contents()

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            category=category,
            magnet_uri=magnet_uri,
            description=description,
        )
