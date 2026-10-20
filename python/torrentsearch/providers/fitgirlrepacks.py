"""FitGirl Repacks search provider — port of FitGirlRepacks.kt."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
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
    TorrentDetailsProvider,
)


def _find_h3_contains_own(root, text: str):
    """Jsoup :containsOwn equivalent for h3 elements."""
    for el in root.find_all("h3"):
        own = "".join(el.find_all(string=True, recursive=False))
        if text.lower() in own.lower():
            return el
    return None


class FitGirlRepacks(
    SearchProvider,
    LatestTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "fitgirlrepacks"
    name = "FitGirl Repacks"
    url = "https://fitgirl-repacks.site"
    supported_categories = {Category.GAMES}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _FitGirlRepacksResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/?s={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        response_html = await self.network.get_text(self.url)
        return self._results_parser.parse(response_html, self.url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _FitGirlRepacksDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _FitGirlRepacksDetailsPageParser.parse(response_html, details_page_url)


class _FitGirlRepacksResultsPageParser:
    LIST_ITEM = "article.category-lossless-repack"
    GAME_NAME = "header > h1.entry-title > a"
    UPLOAD_DATE = "header > div.entry-meta > span.entry-date > a > time"
    MAGNET_URI = 'div.entry-content a[href^="magnet:?xt="]'
    DETAILS_PAGE_URL = GAME_NAME

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
        name_el = H.select_first(list_item, self.DETAILS_PAGE_URL)
        details_page_url = H.attr(name_el, "abs:href", page_url)
        if not details_page_url:
            return None
        game_name = H.own_text(name_el)
        if not game_name:
            return None

        remote_id = list_item.get("id") or ""
        remote_id = remote_id if remote_id.strip() else None
        torrent_id = utils.create_torrent_id(
            self._provider_id,
            remote_id or details_page_url,
        )

        upload_date = None
        date_el = H.select_first(list_item, self.UPLOAD_DATE)
        datetime_attr = H.attr(date_el, "datetime")
        if datetime_attr:
            upload_date = utils.parse_iso(datetime_attr)

        magnet_href = H.attr(H.select_first(list_item, self.MAGNET_URI), "href")
        if magnet_href:
            magnet_uri = MagnetAvailable(value=magnet_href)
        else:
            magnet_uri = MagnetRequiresFetch(url=details_page_url)

        return Torrent(
            id=torrent_id,
            name=game_name,
            upload_date=upload_date,
            category=Category.GAMES,
            magnet_uri=magnet_uri,
            provider_name=self._provider_name,
            details_page_url=details_page_url,
        )


class _FitGirlRepacksDetailsPageParser:
    GAME_NAME = "h1.entry-title"
    UPLOAD_DATE = "time.entry-date"
    UPLOADER = "span.author > a"
    DESCRIPTION = "div.entry-content"
    POSTER = "div.entry-content > p:nth-child(2) > a:nth-child(1) > img"
    MAGNET_URI = 'a[href^="magnet:?xt="]'

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        game_name = H.own_text(H.select_first(soup, cls.GAME_NAME))
        if not game_name:
            return None

        upload_date = None
        date_el = H.select_first(soup, cls.UPLOAD_DATE)
        datetime_attr = H.attr(date_el, "datetime")
        if datetime_attr:
            upload_date = utils.parse_iso(datetime_attr)

        uploader = H.own_text(H.select_first(soup, cls.UPLOADER))
        desc_el = H.select_first(soup, cls.DESCRIPTION)
        description = desc_el.decode_contents() if desc_el is not None else None
        poster_url = H.attr(H.select_first(soup, cls.POSTER), "abs:src", page_url)

        screenshot_urls: list[str] = []
        screenshots_title = _find_h3_contains_own(
            soup, "Screenshots (Click to enlarge)"
        )
        if screenshots_title is not None:
            sibling = screenshots_title.find_next_sibling()
            if sibling is not None:
                for img in sibling.select("img"):
                    src = img.get("src")
                    if src:
                        screenshot_urls.append(str(src))

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=game_name,
            upload_date=upload_date,
            uploader=uploader,
            category=Category.GAMES,
            description=description,
            magnet_uri=magnet_uri,
            poster_url=poster_url,
            screenshot_urls=screenshot_urls,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
