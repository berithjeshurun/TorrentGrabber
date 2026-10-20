"""TorrentDownload search provider — port of TorrentDownload.kt."""

from __future__ import annotations

import re
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


class TorrentDownload(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "torrentdownloadinfo"
    name = "TorrentDownload"
    url = "https://torrentdownload.info"
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
        self._results_page_parser = _TorrentDownloadResultsParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?q={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TorrentDownloadDetailsPageParser.parse(response_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/latest"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)


class _TorrentDownloadResultsParser:
    LIST_ITEM = "table.table2 > tbody > tr:has(span.smallish)"
    TORRENT_NAME = "td:nth-child(1) > div.tt-name > a"
    SIZE = "td:nth-child(3)"
    SEEDERS = "td:nth-child(4)"
    PEERS = "td:nth-child(5)"
    UPLOAD_DATE = "td:nth-child(2)"
    CATEGORY = "td:nth-child(1) > div.tt-name > span"
    DETAILS_PAGE_URL = TORRENT_NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        h3 = H.select_first(soup, "h3")
        if H.own_text(h3) == "No Results Found":
            return []
        return [
            t
            for item in H.select(soup, self.LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item: Tag, page_url: str) -> Optional[Torrent]:
        torrent_name = H.text(H.select_first(list_item, self.TORRENT_NAME))
        if not torrent_name:
            return None
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None

        # detailsPageUrl.dropLastWhile { it != '/' }.dropLast(1).takeLastWhile { it != '/' }
        without_name = details_page_url.rsplit("/", 1)[0]
        info_hash = without_name.rsplit("/", 1)[-1].strip().lower()

        torrent_id = utils.create_torrent_id(self._provider_id, info_hash)
        magnet_uri = utils.create_magnet_uri(info_hash)
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders_raw = H.own_text(H.select_first(list_item, self.SEEDERS))
        peers_raw = H.own_text(H.select_first(list_item, self.PEERS))
        seeders = _to_uint(seeders_raw.replace(",", "") if seeders_raw else None)
        peers = _to_uint(peers_raw.replace(",", "") if peers_raw else None)
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        upload_date = utils.try_parse_relative(upload_raw) if upload_raw else None
        category_raw = H.own_text(H.select_first(list_item, self.CATEGORY))
        category = category_from_raw_string(category_raw) if category_raw else None

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


class _TorrentDownloadDetailsPageParser:
    TORRENT_INFO_TABLE_BODY = "table.torrentinfo > tbody"
    TORRENT_NAME = f"{TORRENT_INFO_TABLE_BODY} > tr:nth-child(1) > td:nth-child(2)"
    SIZE = f"{TORRENT_INFO_TABLE_BODY} > tr:nth-child(6) > td:nth-child(2)"
    SEEDERS = (
        f"{TORRENT_INFO_TABLE_BODY} > tr:nth-child(5) > td:nth-child(2) > span:nth-child(1)"
    )
    PEERS = (
        f"{TORRENT_INFO_TABLE_BODY} > tr:nth-child(5) > td:nth-child(2) > span:nth-child(2)"
    )
    UPLOAD_DATE = f"{TORRENT_INFO_TABLE_BODY} > tr:nth-child(8) > td:nth-child(2)"
    CATEGORY = f"{TORRENT_INFO_TABLE_BODY} > tr:nth-child(4) > td:nth-child(2)"
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = 'a[href^="https://itorrent.net/"]'

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)
        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)
        size = H.own_text(H.select_first(soup, cls.SIZE))

        seeders_raw = H.own_text(H.select_first(soup, cls.SEEDERS))
        seeders = None
        if seeders_raw is not None:
            seeders = _to_uint(seeders_raw.removeprefix("Seeds: ").strip())

        peers_raw = H.own_text(H.select_first(soup, cls.PEERS))
        peers = None
        if peers_raw is not None:
            peers = _to_uint(peers_raw.removeprefix("Leechers: ").strip())

        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "d MMMM yyyy") if upload_raw else None
        )
        category_raw = H.own_text(H.select_first(soup, cls.CATEGORY))
        category = category_from_raw_string(category_raw) if category_raw else None
        file_download_link = H.attr(H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "href")

        return TorrentDetails(
            info_hash=info_hash,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )


def category_from_raw_string(raw: str) -> Category:
    raw = re.sub(r"[^A-Za-z]+", "", raw)
    if raw in {
        "XXX",
        "XXXVideo",
        "XXXHDVideo",
        "XXXPictures",
        "Adult",
        "AdultPornHDVideo",
        "AdultPornPictures",
        "AdultPornVideo",
    }:
        return Category.PORN
    if raw in {"Anime", "AnimeEnglishtranslated", "AnimeAnimeOther"}:
        return Category.ANIME
    if raw in {
        "Applications",
        "ApplicationsAndroid",
        "ApplicationsWindows",
        "Software",
    }:
        return Category.APPS
    if raw in {
        "BooksAcademic",
        "BooksComics",
        "BooksEbooks",
        "BooksEducational",
        "BooksMagazines",
        "BooksFiction",
        "BooksNonfiction",
        "BooksTextbooks",
        "Ebooks",
        "OtherEbooks",
        "OtherComics",
        "AudioBooks",
        "AudioAudiobooks",
    }:
        return Category.BOOKS
    if raw in {"Games", "GamesWindows"}:
        return Category.GAMES
    if raw in {
        "Movies",
        "MoviesAction",
        "MoviesConcerts",
        "MoviesCrime",
        "MoviesDocumentary",
        "MoviesDubbedMovies",
        "MoviesHighresMovies",
        "MoviesMusicvideos",
        "MoviesThriller",
        "VideoMovies",
    }:
        return Category.MOVIES
    if raw in {
        "Music",
        "MusicHardrock",
        "MusicMp",
        "MusicFLAC",
        "MusicLossless",
        "MusicRB",
        "MusicTranceHouseDance",
        "VideoMusic",
        "AudioMusic",
    }:
        return Category.MUSIC
    if raw in {"TV", "TVBBC", "TVshows", "Television"}:
        return Category.SERIES
    return Category.OTHER


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
