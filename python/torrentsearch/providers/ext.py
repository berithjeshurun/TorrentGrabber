"""Ext search provider — port of Ext.kt."""

from __future__ import annotations

import hashlib
import json
import time
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


def _compute_hmac(torrent_id: str, timestamp: int, page_token: str) -> str:
    data = f"{torrent_id}|{timestamp}|{page_token}".encode()
    return hashlib.sha256(data).hexdigest()


def _category_from_raw(raw: str) -> Category:
    return {
        "anime": Category.ANIME,
        "applications": Category.APPS,
        "books": Category.BOOKS,
        "games": Category.GAMES,
        "movies": Category.MOVIES,
        "music": Category.MUSIC,
        "other": Category.OTHER,
        "tv": Category.SERIES,
        "xxx": Category.PORN,
    }.get(raw, Category.OTHER)


class Ext(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "extdotto"
    name = "Ext"
    url = "https://ext.to"
    cloudflare_solver_url = f"{url}/browse/?q=ubuntu"
    safety = SearchProviderSafety.SAFE
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
        Category.PORN,
        Category.SERIES,
    }
    enabled_by_default = False
    is_cloudflare_protected = True

    _CATEGORY_MAP = {
        Category.ANIME: 7,
        Category.APPS: 5,
        Category.BOOKS: 6,
        Category.GAMES: 4,
        Category.MOVIES: 1,
        Category.MUSIC: 3,
        Category.OTHER: 8,
        Category.PORN: 10,
        Category.SERIES: 2,
    }

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _ExtResultsPageParser(self.id, self.name)
        self._details_parser = _ExtDetailsPageParser(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        parts = [self.url, "/browse/?"]
        cat_id = self._CATEGORY_MAP.get(category)
        if cat_id is not None:
            parts.append(f"cat={cat_id}&")
        parts.append(f"q={query}")
        request_url = "".join(parts)
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return await self._details_parser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        parts = [
            self.url,
            "/browse/",
            "?sort=age",
            "&order=desc",
            "&age=4",
        ]
        if category is not Category.ALL:
            cat_id = self._CATEGORY_MAP.get(category)
            if cat_id is not None:
                parts.append(f"&cat={cat_id}")
        request_url = "".join(parts)
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        parts = [
            self.url,
            "/browse/",
            "?sort=seeds",
            "&order=desc",
            "&age=4",
        ]
        if category is not Category.ALL:
            cat_id = self._CATEGORY_MAP.get(category)
            if cat_id is not None:
                parts.append(f"&cat={cat_id}")
        request_url = "".join(parts)
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = await self._details_parser.get_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _ExtResultsPageParser:
    LIST_ITEM = "table.search-table > tbody > tr"
    TORRENT_NAME = "td:nth-child(1) > div:nth-child(1) > a.torrent-title-link"
    SIZE = "td:nth-child(2) > div > span:nth-child(2)"
    SEEDERS = "td:nth-child(5) > div > span:nth-child(2)"
    PEERS = "td:nth-child(6) > div > span:nth-child(2)"
    UPLOAD_DATE = "td:nth-child(4) > div > span:nth-child(2)"
    CATEGORY = "td:nth-child(1) > div:nth-child(1) > div.related-posted > a:nth-child(2)"
    TORRENT_ID = "td:nth-child(1) > div:nth-child(2) > a.search-magnet-btn"
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
        torrent_name = H.text(name_el)
        if not torrent_name:
            return None
        details_page_url = H.attr(name_el, "abs:href", page_url)
        if not details_page_url:
            return None

        torrent_remote_id = H.attr(H.select_first(list_item, self.TORRENT_ID), "data-id")
        if not torrent_remote_id:
            return None
        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(list_item, self.PEERS)))

        upload_date = None
        date_el = H.select_first(list_item, self.UPLOAD_DATE)
        title = H.attr(date_el, "title")
        if title:
            upload_date = utils.parse_date(title, "dd MMMM yyyy")

        category = None
        cat_el = H.select_first(list_item, self.CATEGORY)
        href = H.attr(cat_el, "href")
        if href:
            raw = href.strip("/")
            category = _category_from_raw(raw)

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _ExtDetailsPageParser:
    SESSION_ID = 'meta[name="csrf-token"]'
    TORRENT_ID = "a.detail-magnet-link.download-btn-magnet"
    DETAILS_PAGE_CARD = "div.detal-page-left-block > div.card > div.card-body"
    TORRENT_NAME = f"{DETAILS_PAGE_CARD} .card-title"
    SIZE = f"{DETAILS_PAGE_CARD} .content-size"
    SEEDERS = f"{DETAILS_PAGE_CARD} #seed-counter"
    PEERS = f"{DETAILS_PAGE_CARD} #leech-counter"
    UPLOAD_DATE = f"{DETAILS_PAGE_CARD} div.detail-torrent-poster-info > span:nth-child(1)"
    CATEGORY = f"{DETAILS_PAGE_CARD} div.detail-torrent-poster-info > a:nth-child(3)"
    UPLOADER = f"{DETAILS_PAGE_CARD} div.detail-torrent-poster-info span.external-user"
    LAST_CHECKED = f"{DETAILS_PAGE_CARD} span.detail-update-date > strong"
    TORRENT_SLUG = f"{DETAILS_PAGE_CARD} a.js-dscr"
    MOVIE_PLOT = f"{DETAILS_PAGE_CARD} div.movie-info > div.plot-block"
    SERIES_PLOT = f"{DETAILS_PAGE_CARD} div.plot-info > div.block-plot-tv"
    POSTER_URL = f"{DETAILS_PAGE_CARD} div.poster-block > a > img"
    POSTER_URL_1 = f"{DETAILS_PAGE_CARD} img.detail-torrent-image"

    def __init__(self, network: NetworkClient):
        self._network = network

    async def parse(self, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        torrent_id = H.attr(H.select_first(soup, self.TORRENT_ID), "data-id")
        if not torrent_id:
            return None
        session_id = H.attr(H.select_first(soup, self.SESSION_ID), "content")
        if not session_id:
            return None
        page_token = self._extract_page_token(soup)
        if not page_token:
            return None
        magnet_uri = await self._fetch_magnet_uri(torrent_id, session_id, page_token)
        if not magnet_uri:
            return None

        torrent_name = H.own_text(H.select_first(soup, self.TORRENT_NAME))
        if not torrent_name:
            return None

        size_raw = H.own_text(H.select_first(soup, self.SIZE))
        size = size_raw.removeprefix("Size: ") if size_raw else None
        seeders = _uint(H.own_text(H.select_first(soup, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(soup, self.PEERS)))

        upload_date = None
        date_el = H.select_first(soup, self.UPLOAD_DATE)
        title = H.attr(date_el, "title")
        if title:
            upload_date = utils.parse_date(title, "dd MMMM yyyy")

        category = None
        cat_el = H.select_first(soup, self.CATEGORY)
        href = H.attr(cat_el, "href")
        if href:
            category = _category_from_raw(href.strip("/"))

        uploader = H.own_text(H.select_first(soup, self.UPLOADER))
        last_checked_raw = H.own_text(H.select_first(soup, self.LAST_CHECKED))
        last_checked = (
            utils.try_parse_relative(last_checked_raw) if last_checked_raw else None
        )

        if category is Category.MOVIES:
            description = H.own_text(H.select_first(soup, self.MOVIE_PLOT))
        elif category is Category.SERIES:
            description = H.own_text(H.select_first(soup, self.SERIES_PLOT))
        else:
            slug = H.attr(H.select_first(soup, self.TORRENT_SLUG), "data-code")
            description = (
                await self._get_description(torrent_id, slug) if slug else None
            )

        poster_el = H.select_first(soup, self.POSTER_URL) or H.select_first(
            soup, self.POSTER_URL_1
        )
        poster_url = H.attr(poster_el, "abs:src", page_url)
        if poster_url and poster_url.endswith("no-torrent-image.png"):
            poster_url = None

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            magnet_uri=magnet_uri,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            uploader=uploader,
            last_checked=last_checked,
            description=description,
            poster_url=poster_url,
        )

    def _extract_page_token(self, soup) -> Optional[str]:
        for script in H.select(soup, "script"):
            data = (script.string or "").strip()
            if not data:
                continue
            first_line = data.splitlines()[0] if data.splitlines() else ""
            if first_line.startswith("window.pageToken"):
                line = first_line.removesuffix(";")
                return line.rsplit("'", 1)[-1] if "'" in line else None
        return None

    async def get_magnet_uri(self, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        torrent_id = H.attr(H.select_first(soup, self.TORRENT_ID), "data-id")
        if not torrent_id:
            return None
        session_id = H.attr(H.select_first(soup, self.SESSION_ID), "content")
        if not session_id:
            return None
        page_token = self._extract_page_token(soup)
        if not page_token:
            return None
        return await self._fetch_magnet_uri(torrent_id, session_id, page_token)

    async def _fetch_magnet_uri(
        self, torrent_id: str, session_id: str, page_token: str
    ) -> Optional[str]:
        timestamp = int(time.time())
        hmac_token = _compute_hmac(torrent_id, timestamp, page_token)
        response_text = await self._network.submit_form(
            url="https://ext.to/ajax/getTorrentMagnet.php",
            form_data={
                "torrent_id": torrent_id,
                "download_type": "magnet",
                "timestamp": str(timestamp),
                "hmac": hmac_token,
                "sessid": session_id,
            },
        )
        try:
            obj = json.loads(response_text)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict):
            url = obj.get("url")
            return url if isinstance(url, str) else None
        return None

    async def _get_description(
        self, torrent_id: str, torrent_slug: str
    ) -> Optional[str]:
        data = await self._network.get_json(
            url=f"https://ext.to/ajax/torrentDescription.php?id={torrent_id}&code={torrent_slug}",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        if isinstance(data, dict):
            desc = data.get("data")
            return desc if isinstance(desc, str) else None
        return None
