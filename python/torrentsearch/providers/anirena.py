"""AniRena search provider — port of AniRena.kt."""

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


def _category_from_raw_string(raw: str) -> Category:
    return {
        "Anime": Category.ANIME,
        "Manga": Category.BOOKS,
        "Audio": Category.MUSIC,
        "Literature": Category.BOOKS,
        "Live Action": Category.SERIES,
        "Software": Category.APPS,
        "Hentai": Category.PORN,
        "Other": Category.OTHER,
    }.get(raw, Category.OTHER)


async def _fetch_magnet_uri(source_url: str, network: NetworkClient) -> Optional[str]:
    response = await network.get(source_url)
    return response.headers.get("Location")


class AniRena(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "anirena"
    name = "AniRena"
    url = "https://anirena.com"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.MUSIC,
        Category.PORN,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.ANIME: "anime",
            Category.APPS: "software",
            Category.BOOKS: "manga",
            Category.MUSIC: "audio",
            Category.PORN: "hentai",
            Category.SERIES: "live",
            Category.OTHER: "other",
        }
        self._results_page_parser = _AniRenaResultsPageParser(self.id, self.name)
        self._details_page_parser = _AniRenaDetailsPageParser(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}?q={query}&page=1"
        if category is not Category.ALL:
            cat = self._category_map.get(category)
            if cat is not None:
                request_url += f"&cat={cat}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        response_html = await self.network.get_text(self.url)
        return self._results_page_parser.parse(html=response_html, page_url=self.url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        return await self.get_latest_torrents(category)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return await self._details_page_parser.parse(
            html=response_html, page_url=details_page_url
        )

    async def get_magnet_uri(self, url: str) -> str:
        response = await self.network.get(url)
        location = response.headers.get("Location")
        if not location:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return location


class _AniRenaResultsPageParser:
    LIST_ITEM = "table.tl-table > tbody > tr"
    TORRENT_NAME = "td.col-name > div.tl-name-wrap > a.tl-torrent-name"
    TORRENT_ID = TORRENT_NAME
    SIZE = "td.col-size"
    SEEDERS = "td.col-se > span.tl-se"
    PEERS = "td.col-le > span.tl-le"
    CATEGORY = "td.col-cat"
    MAGNET_URI_SRC_URL = "td.col-actions > div.tl-actions > a:nth-child(1)"
    FILE_DOWNLOAD_LINK = "td.col-actions > div.tl-actions > a:nth-child(2)"
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
        magnet_uri_source_url = H.attr(
            H.select_first(list_item, self.MAGNET_URI_SRC_URL),
            "abs:href",
            page_url,
        )
        if not magnet_uri_source_url:
            return None

        torrent_remote_id = H.attr(
            H.select_first(list_item, self.TORRENT_ID), "data-torrent-id"
        )
        if not torrent_remote_id:
            # Kotlin: removeSurrounding("/torrrents/", "/magnet") — typo preserved
            prefix, suffix = "/torrrents/", "/magnet"
            if magnet_uri_source_url.startswith(prefix) and magnet_uri_source_url.endswith(
                suffix
            ):
                torrent_remote_id = magnet_uri_source_url[len(prefix) : -len(suffix)]
            else:
                torrent_remote_id = magnet_uri_source_url

        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders_raw = H.own_text(H.select_first(list_item, self.SEEDERS))
        peers_raw = H.own_text(H.select_first(list_item, self.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_date = None
        created_ts = list_item.get("data-created-ts")
        if created_ts and str(created_ts).strip():
            try:
                upload_date = utils.epoch_second_to_datetime(int(str(created_ts)))
            except ValueError:
                upload_date = None

        category = None
        cat_title = H.attr(H.select_first(list_item, self.CATEGORY), "title")
        if cat_title and cat_title.strip():
            # takeWhile { it != '/' }
            raw = ""
            for ch in cat_title:
                if ch == "/":
                    break
                raw += ch
            raw = raw.strip()
            if raw:
                category = _category_from_raw_string(raw)

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
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=magnet_uri_source_url),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _AniRenaDetailsPageParser:
    INFO_HASH = "code.td-ov-stat-hash"
    TORRENT_NAME = "h1.td-title"
    SIZE = "div.td-ov-stats > div.td-ov-stat:nth-child(4) > div.td-ov-stat-num"
    SEEDERS = "div.td-ov-stats > div.td-ov-stat--se > div.td-ov-stat-num"
    PEERS = "div.td-ov-stats > div.td-ov-stat--le > div.td-ov-stat-num"
    UPLOAD_DATE = "div.td-ov-meta > div.td-ov-meta-val:nth-child(6) > span"
    CATEGORY = "div.td-ov-meta-val:nth-child(2) a.td-cat-name.td-cat-link"
    UPLOADER = "div.td-ov-meta > div.td-ov-meta-val:nth-child(4)"
    MAGNET_URI = "div.td-actions > a:nth-child(1)"
    FILE_DOWNLOAD_LINK = "div.td-actions > a:nth-child(2)"
    DESCRIPTION = "script#td-description-raw"
    POSTER_URL = "div.td-anime-poster > img"

    def __init__(self, network: NetworkClient):
        self._network = network

    async def parse(self, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        info_hash_raw = H.own_text(H.select_first(soup, self.INFO_HASH))
        if not info_hash_raw:
            return None
        info_hash = info_hash_raw.lower()

        torrent_name = H.text(H.select_first(soup, self.TORRENT_NAME))
        if not torrent_name:
            return None

        size = H.own_text(H.select_first(soup, self.SIZE))
        seeders_raw = H.own_text(H.select_first(soup, self.SEEDERS))
        peers_raw = H.own_text(H.select_first(soup, self.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_date = None
        upload_el = H.select_first(soup, self.UPLOAD_DATE)
        data_utc = H.attr(upload_el, "data-utc")
        if data_utc:
            upload_date = utils.parse_date(data_utc, "yyyy-MM-dd HH:mm")

        cat_raw = H.own_text(H.select_first(soup, self.CATEGORY))
        category = _category_from_raw_string(cat_raw) if cat_raw else None
        uploader = H.own_text(H.select_first(soup, self.UPLOADER))

        magnet_href = H.attr(
            H.select_first(soup, self.MAGNET_URI), "abs:href", page_url
        )
        magnet_uri = None
        if magnet_href:
            magnet_uri = await _fetch_magnet_uri(magnet_href, self._network)
        if not magnet_uri:
            magnet_uri = utils.create_magnet_uri(info_hash)

        file_download_link = H.attr(
            H.select_first(soup, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

        description = None
        desc_el = H.select_first(soup, self.DESCRIPTION)
        if desc_el is not None:
            # Jsoup Element.data() for script content
            data = desc_el.string or "".join(desc_el.stripped_strings)
            if data is None:
                data = desc_el.get_text()
            if data.startswith('"') and data.endswith('"'):
                data = data[1:-1]
            description = data

        poster_url = H.attr(
            H.select_first(soup, self.POSTER_URL), "abs:src", page_url
        )

        return TorrentDetails(
            info_hash=info_hash,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            uploader=uploader,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
        )
