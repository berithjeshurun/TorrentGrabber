"""LinuxTracker search provider — port of LinuxTracker.kt."""

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


class LinuxTracker(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "linuxtracker"
    name = "LinuxTracker"
    url = "https://linuxtracker.org"
    supported_categories = {Category.APPS}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _LinuxTrackerResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = (
            f"{self.url}/index.php?page=torrents&search={query}&category=0&active=0"
        )
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _LinuxTrackerDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/torrents/"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        return await self.get_latest_torrents(category)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _LinuxTrackerDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _LinuxTrackerResultsPageParser:
    LIST_ITEM = "table.torrent-table > tbody > tr"
    TORRENT_NAME = "td.torrent-name-cell > a.torrent-name"
    SIZE = "td:nth-child(4)"
    SEEDERS = "td.seeds > strong"
    PEERS = "td.leeches > strong"
    UPLOAD_DATE = "td:nth-child(3)"
    DETAILS_PAGE_URL = TORRENT_NAME

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
        name_el = H.select_first(list_item, self.TORRENT_NAME)
        torrent_name = H.own_text(name_el)
        if not torrent_name:
            return None
        details_page_url = H.attr(name_el, "abs:href", page_url)
        if not details_page_url or not details_page_url.strip():
            return None

        # https://linuxtracker.org/torrents/5b1e0d988fc7a0c9e99bd852071681a59974b39f/
        torrent_remote_id = details_page_url.rstrip("/").rsplit("/", 1)[-1]
        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(list_item, self.PEERS)))

        upload_date = None
        date_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if date_raw:
            upload_date = utils.parse_date(date_raw.strip(), "MMM d, yyyy")

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.APPS,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _LinuxTrackerDetailsPageParser:
    TORRENT_NAME = "div.detail-title-block > h2"
    SIZE = "dl.torrent-meta > div:nth-child(3) > dd"
    SEEDERS = "div.detail-stats strong.seeds"
    PEERS = "div.detail-stats strong.leeches"
    UPLOAD_DATE = "dl.torrent-meta > div:nth-child(2) > dd"
    DESCRIPTION = "div.torrent-description"
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = "a.torrent-action.primary-action"
    SCREENSHOT = "div.torrent-screenshot-grid img"

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        size = H.own_text(H.select_first(soup, cls.SIZE))
        seeders = _uint(H.own_text(H.select_first(soup, cls.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(soup, cls.PEERS)))

        upload_date = None
        date_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        if date_raw:
            upload_date = utils.parse_date(date_raw, "MMM d, yyyy HH:mm")

        desc_el = H.select_first(soup, cls.DESCRIPTION)
        description = desc_el.decode_contents() if desc_el is not None else None
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        screenshot_urls = [
            src
            for img in H.select(soup, cls.SCREENSHOT)
            if (src := H.attr(img, "abs:src", page_url))
        ]

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            magnet_uri=magnet_uri,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=Category.APPS,
            file_download_link=file_download_link,
            description=description,
            screenshot_urls=screenshot_urls,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
