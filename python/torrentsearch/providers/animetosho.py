"""AnimeTosho search provider — port of AnimeTosho.kt."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from bs4 import NavigableString, Tag

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
from ..provider import SearchProvider, TorrentDetailsProvider

DATE_PREFIX = "Date/time submitted: "
STATS_REGEX = re.compile(r"\[(\d+)↑/(\d+)↓]")


class AnimeTosho(SearchProvider, TorrentDetailsProvider):
    id = "animetosho"
    name = "AnimeTosho"
    url = "https://animetosho.org"
    supported_categories = {Category.ANIME}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _AnimeToshoResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?q={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _AnimeToshoDetailsPageParser.parse(
            html=response_html, base_url=details_page_url
        )


class _AnimeToshoResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str) -> list[Torrent]:
        soup = H.parse_html(html)
        return [
            t
            for entry in H.select(soup, "div.home_list_entry")
            if (t := self._parse_entry_div(entry)) is not None
        ]

    def _parse_entry_div(self, entry_div: Tag) -> Optional[Torrent]:
        anchor = H.select_first(entry_div, "div.link > a")
        if anchor is None:
            return None
        name = H.text(anchor)
        if not name:
            return None
        details_page_url = H.attr(anchor, "href")
        if not details_page_url or not details_page_url.strip():
            return None

        links = H.select_first(entry_div, "div.links")
        if links is None:
            return None
        magnet_el = H.select_first(links, 'a[href^="magnet:"]')
        magnet_uri = H.attr(magnet_el, "href")
        if not magnet_uri:
            return None
        file_download_link = H.attr(H.select_first(links, "a.dllink"), "href")

        torrent_id = utils.create_torrent_id(self._provider_id, details_page_url)
        size = H.own_text(H.select_first(entry_div, "div.size"))
        seeders, peers = self._parse_seeds_and_peers(entry_div)
        upload_date = self._parse_upload_date(entry_div)

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self._provider_name,
            upload_date=upload_date,
            category=Category.ANIME,
            details_page_url=details_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
        )

    def _parse_upload_date(self, entry_div: Tag) -> Optional[datetime]:
        date_el = H.select_first(entry_div, "div.date")
        raw = H.attr(date_el, "title")
        if not raw:
            return None
        if raw.startswith(DATE_PREFIX):
            raw = raw[len(DATE_PREFIX) :]
        raw = raw.strip()
        if not raw:
            return None

        if raw.startswith("Today"):
            return utils.get_today_date()
        if raw.startswith("Yesterday"):
            return utils.get_yesterday_date()
        date_part = raw.split(" ", 1)[0]
        return utils.parse_date(date_part, "d/M/yyyy")

    def _parse_seeds_and_peers(self, entry_div: Tag) -> tuple[int, int]:
        links = H.select_first(entry_div, "div.links")
        if links is None:
            return 0, 0
        span = None
        for candidate in H.select(links, "span"):
            if candidate.has_attr("title"):
                span = candidate
                break
        if span is None:
            return 0, 0
        span_text = H.own_text(span) or ""
        match = STATS_REGEX.search(span_text)
        if not match:
            return 0, 0
        try:
            seeds = int(match.group(1))
        except ValueError:
            seeds = 0
        try:
            peers = int(match.group(2))
        except ValueError:
            peers = 0
        return seeds, peers


class _AnimeToshoDetailsPageParser:
    TORRENT_NAME = "#title"
    SIZE = 'span[title^="File size:"]'
    SEEDERS = 'td[title="Seeders"][align="right"]'
    PEERS = 'td[title="Leechers"][align="right"]'
    UPLOAD_DATE = "#content > table:nth-of-type(1) > tbody > tr:nth-child(2) > td"
    SCREENSHOT = "a.screenthumb"
    MAGNET_URI = 'a[href^="magnet:"]'
    FILE_DOWNLOAD_LINK = 'a[href^="https://animetosho.org/storage/torrent"]'

    @classmethod
    def parse(cls, html: str, base_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, base_url)

        name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not name:
            return None
        magnet_uri_elem = H.select_first(soup, cls.MAGNET_URI)
        if magnet_uri_elem is None:
            return None
        magnet_uri = H.attr(magnet_uri_elem, "href")
        if not magnet_uri or not magnet_uri.strip():
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)

        unprocessed_size = H.own_text(H.select_first(soup, cls.SIZE))
        if unprocessed_size is None:
            nxt = magnet_uri_elem.next_sibling
            if isinstance(nxt, NavigableString):
                unprocessed_size = str(nxt)
        size = None
        if unprocessed_size is not None:
            size = "".join(c for c in unprocessed_size.strip() if c not in "()|")
            size = size.strip() or None

        seeders_raw = H.own_text(H.select_first(soup, cls.SEEDERS))
        peers_raw = H.own_text(H.select_first(soup, cls.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = None
        if upload_raw:
            if upload_raw.startswith("Today"):
                upload_date = utils.get_today_date()
            elif upload_raw.startswith("Yesterday"):
                upload_date = utils.get_yesterday_date()
            else:
                upload_date = utils.parse_date(upload_raw, "dd/MM/yyyy HH:mm")

        screenshot_urls = [
            href
            for el in H.select(soup, cls.SCREENSHOT)
            if (href := H.attr(el, "href"))
        ]
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
            category=Category.ANIME,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            screenshot_urls=screenshot_urls,
        )
