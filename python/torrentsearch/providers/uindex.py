"""UIndex search provider — port of UIndex.kt."""

from __future__ import annotations

from datetime import datetime
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


class UIndex(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "uindex"
    name = "UIndex"
    url = "https://uindex.org"
    cloudflare_solver_url = f"{url}/search.php?search=ubuntu"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.PORN,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
    enabled_by_default = True

    _CATEGORY_MAP = {
        Category.ALL: 0,
        Category.BOOKS: 0,
        Category.ANIME: 7,
        Category.APPS: 5,
        Category.GAMES: 3,
        Category.MOVIES: 1,
        Category.MUSIC: 4,
        Category.PORN: 6,
        Category.SERIES: 2,
        Category.OTHER: 8,
    }

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _UIndexResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP[category]
        request_url = f"{self.url}/search.php?search={query}&c={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _UIndexDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP.get(
            category, self._CATEGORY_MAP[Category.ALL]
        )
        request_url = f"{self.url}/search.php?c={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP.get(
            category, self._CATEGORY_MAP[Category.ALL]
        )
        request_url = f"{self.url}/top.php?t=24h&c={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)


class _UIndexResultsPageParser:
    LIST_ITEM_CONTAINER = "table.sr-table, table.top-table"
    LIST_ITEM = "tbody > tr"
    NAME = "td.sr-col-name > a.sr-torrent-link"
    SIZE = "td.sr-col-size"
    SEEDERS = "td.sr-col-seeders > span.sr-seed"
    PEERS = "td.sr-col-leechers > span.sr-leech"
    UPLOAD_DATE = "td.sr-col-uploaded"
    CATEGORY = "td.sr-col-cat > a.sr-cat-badge"
    MAGNET_URI = "td.sr-col-name > a.sr-magnet"
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
        name = H.text(H.select_first(list_item, self.NAME))
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, self.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders_raw = H.own_text(H.select_first(list_item, self.SEEDERS))
        peers_raw = H.own_text(H.select_first(list_item, self.PEERS))
        seeders = _to_uint("".join(c for c in seeders_raw if c != ",") if seeders_raw else None)
        peers = _to_uint("".join(c for c in peers_raw if c != ",") if peers_raw else None)
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        upload_date = utils.try_parse_relative(upload_raw) if upload_raw else None
        category_raw = H.own_text(H.select_first(list_item, self.CATEGORY))
        category = category_from_raw_string(category_raw) if category_raw else None
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )

        torrent_remote_id = (
            details_page_url.rsplit("=", 1)[-1] if details_page_url else None
        )
        source_id = torrent_remote_id or utils.get_info_hash_from_magnet_uri(magnet_uri)
        torrent_id = utils.create_torrent_id(self._provider_id, source_id)

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
            details_page_url=details_page_url,
        )


class _UIndexDetailsPageParser:
    NAME = ".dt-title"
    SIZE = (
        "#content > div.dt-info-card > div > div:nth-child(1) > "
        "div:nth-child(2) > span.dt-info-value"
    )
    SEEDERS = ".dt-seed"
    PEERS = ".dt-leech"
    UPLOAD_DATE = (
        "#content > div.dt-info-card > div > div:nth-child(1) > "
        "div:nth-child(3) > span.dt-info-value > span.dt-info-dim"
    )
    CATEGORY = ".sr-cat-badge"
    PEERS_UPDATED = (
        "#content > div.dt-info-card > div > div:nth-child(2) > "
        "div:nth-child(3) > span.dt-info-value > span.dt-info-dim"
    )
    DESCRIPTION = ".dt-descr-body"
    THUMBNAIL = ".tmdb-poster > img"
    MAGNET_URI = "a.dt-download-btn"

    @classmethod
    def parse(cls, response_html: str, base_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(response_html, base_url)

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
        seeders = _to_uint(
            "".join(c for c in seeders_raw if c != ",") if seeders_raw else None
        )
        peers = _to_uint(
            "".join(c for c in peers_raw if c != ",") if peers_raw else None
        )
        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = cls._parse_date(upload_raw) if upload_raw else None
        category_raw = H.own_text(H.select_first(soup, cls.CATEGORY))
        category = category_from_raw_string(category_raw) if category_raw else None

        last_checked_el = H.select_first(soup, cls.PEERS_UPDATED)
        last_checked_raw = H.text(last_checked_el)
        last_checked = None
        if last_checked_raw and last_checked_raw.strip():
            last_checked = cls._parse_date(last_checked_raw)

        description_el = H.select_first(soup, cls.DESCRIPTION)
        description = description_el.decode_contents() if description_el else None
        thumbnail_url = H.attr(
            H.select_first(soup, cls.THUMBNAIL), "abs:src", base_url
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
            description=description,
            poster_url=thumbnail_url,
        )

    @staticmethod
    def _parse_date(date: str) -> Optional[datetime]:
        cleaned = date
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = cleaned[1:-1]
        return utils.parse_date(cleaned, "yyyy-MM-dd HH:mm:ss")


def category_from_raw_string(raw: str) -> Category:
    mapping = {
        "Anime": Category.ANIME,
        "Apps": Category.APPS,
        "Games": Category.GAMES,
        "Movies": Category.MOVIES,
        "Music": Category.MUSIC,
        "XXX": Category.PORN,
        "TV": Category.SERIES,
        "Other": Category.OTHER,
    }
    return mapping.get(raw, Category.OTHER)


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
