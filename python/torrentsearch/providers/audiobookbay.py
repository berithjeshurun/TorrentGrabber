"""AudioBookBay search provider — port of AudioBookBay.kt."""

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
    TorrentDetailsProvider,
)


def _select_first_contains_own(soup: Tag, tag: str, needle: str) -> Optional[Tag]:
    for el in soup.find_all(tag):
        ot = H.own_text(el)
        if ot is not None and needle in ot:
            return el
    return None


class AudioBookBay(
    SearchProvider,
    LatestTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "audiobookbay"
    name = "AudioBookBay"
    url = "https://audiobookbay.lu"
    supported_categories = {Category.BOOKS}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _AudioBookBayResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/?s={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        response_html = await self.network.get_text(self.url)
        return self._results_page_parser.parse(html=response_html, page_url=self.url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _AudioBookBayDetailsPageParser.extract_magnet_uri(details_page_html)
        if not magnet:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _AudioBookBayDetailsPageParser.parse(response_html)


class _AudioBookBayResultsPageParser:
    LIST_ITEM = "div.post"
    TORRENT_NAME = "div.postTitle > h2 > a"
    TORRENT_INFO = "div.postContent > p:nth-child(3)"
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
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None

        torrent_id = utils.create_torrent_id(self._provider_id, details_page_url)

        info_el = H.select_first(list_item, self.TORRENT_INFO)
        torrent_info = None
        if info_el is not None:
            # Jsoup wholeText() — text with newlines preserved
            torrent_info = info_el.get_text(separator="\n").splitlines()

        size = None
        upload_date = None
        if torrent_info is not None:
            for line in torrent_info:
                if line.startswith("File Size: "):
                    size = line[len("File Size: ") :].strip()
                    if size.endswith("s"):
                        size = size[:-1]
                elif line.startswith("Posted: "):
                    raw = line[len("Posted: ") :].strip()
                    upload_date = utils.parse_date(raw, "d MMM yyyy")

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            category=Category.BOOKS,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _AudioBookBayDetailsPageParser:
    TORRENT_NAME = "div.postTitle > h1"
    SIZE_NEEDLE = "File Size:"
    SIZE_ALT_NEEDLE = "Combined File Size:"
    UPLOAD_DATE_NEEDLE = "Creation Date:"
    UPLOADER = "div.postContent > div:nth-child(1) > p:nth-child(1) > a"
    INFO_HASH_NEEDLE = "Info Hash:"
    DESCRIPTION = "div.desc"
    POSTER_URL = 'img[itemprop="image"]'

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None

        info_hash_td = _select_first_contains_own(soup, "td", cls.INFO_HASH_NEEDLE)
        if info_hash_td is None:
            return None
        sib = info_hash_td.find_next_sibling()
        info_hash = H.own_text(sib) if isinstance(sib, Tag) else None
        if not info_hash:
            return None

        size_td = _select_first_contains_own(soup, "td", cls.SIZE_NEEDLE)
        if size_td is None:
            size_td = _select_first_contains_own(soup, "td", cls.SIZE_ALT_NEEDLE)
        size = None
        if size_td is not None:
            size_sib = size_td.find_next_sibling()
            if isinstance(size_sib, Tag):
                size = H.text(size_sib)
                if size and size.endswith("s"):
                    size = size[:-1]

        upload_date = None
        upload_td = _select_first_contains_own(soup, "td", cls.UPLOAD_DATE_NEEDLE)
        if upload_td is not None:
            upload_sib = upload_td.find_next_sibling()
            if isinstance(upload_sib, Tag):
                raw = H.own_text(upload_sib)
                if raw:
                    upload_date = utils.parse_rfc1123(raw.strip())

        uploader = H.own_text(H.select_first(soup, cls.UPLOADER))
        desc_el = H.select_first(soup, cls.DESCRIPTION)
        description = desc_el.decode_contents() if desc_el is not None else None
        poster_url = H.attr(H.select_first(soup, cls.POSTER_URL), "src")

        return TorrentDetails(
            info_hash=info_hash,
            magnet_uri=utils.create_magnet_uri(info_hash),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            category=Category.BOOKS,
            uploader=uploader,
            description=description,
            poster_url=poster_url,
        )

    @classmethod
    def extract_magnet_uri(cls, html: str) -> Optional[str]:
        soup = H.parse_html(html)
        info_hash_td = _select_first_contains_own(soup, "td", cls.INFO_HASH_NEEDLE)
        if info_hash_td is None:
            return None
        sib = info_hash_td.find_next_sibling()
        info_hash = H.own_text(sib) if isinstance(sib, Tag) else None
        if not info_hash:
            return None
        return utils.create_magnet_uri(info_hash)
