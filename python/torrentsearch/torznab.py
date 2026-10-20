"""Torznab helpers — ports of TorznabCategoryMapper, TorznabResultsXmlParser, TorznabUtils."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from . import utils
from .models import Category, MagnetAvailable, Torrent

MIME_TYPE_TORRENT = "application/x-bittorrent"
CUSTOM_CATEGORY_RANGE_START = 100000
TORZNAB_FUNCTION_CAPS = "caps"
TORZNAB_FUNCTION_SEARCH = "search"


def normalize_api_url(url: str) -> str:
    trimmed = url.rstrip("/")
    return trimmed if trimmed.endswith("api") else f"{trimmed}/api"


class TorznabCategoryMapper:
    """
    Category to Torznab category ids map.

    The mapped ids are used only inside the request URL.

    See:
    - https://github.com/Jackett/Jackett/wiki/Jackett-Categories
    - https://newznab.readthedocs.io/en/latest/misc/api.html#predefined-categories
    """

    _CATEGORY_TO_IDS = {
        Category.ANIME: [5070],
        Category.APPS: [4000, 4010, 4020, 4030, 4040, 4050, 4060, 4070],
        Category.BOOKS: [7000, 7010, 7020, 7030, 7040, 7050, 7060],
        Category.GAMES: [1000, 4050],
        Category.MOVIES: [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080],
        Category.MUSIC: [3000, 3010, 3040, 3050, 3060],
        Category.PORN: [6000, 6010, 6020, 6030, 6040, 6045, 6050, 6060, 6070, 6080, 6090],
        Category.SERIES: [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5080],
        Category.OTHER: [8000, 8010, 8020],
    }

    @classmethod
    def get_cat_param_value(cls, category: Category) -> Optional[str]:
        if category is Category.ALL:
            return None
        ids = cls._CATEGORY_TO_IDS.get(category)
        if ids is None:
            return None
        return ",".join(str(i) for i in ids)

    @classmethod
    def get_category_from_id(cls, category_id: int) -> Category:
        if 1000 <= category_id <= 1999:
            return Category.GAMES
        if 2000 <= category_id <= 2999:
            return Category.MOVIES
        if 3000 <= category_id <= 3999:
            return Category.BOOKS if category_id == 3030 else Category.MUSIC
        if 4000 <= category_id <= 4999:
            return Category.GAMES if category_id == 4050 else Category.APPS
        if 5000 <= category_id <= 5999:
            return Category.ANIME if category_id == 5070 else Category.SERIES
        if 6000 <= category_id <= 6999:
            return Category.PORN
        if 7000 <= category_id <= 7999:
            return Category.BOOKS
        if 8000 <= category_id <= 8999:
            return Category.OTHER
        return Category.OTHER


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.split(":", 1)[-1]
    return tag


def _is_torznab_attr(tag: str) -> bool:
    # Kotlin parses with namespaces disabled → tag name "torznab:attr".
    # ElementTree may expand to "{ns}attr".
    return tag == "torznab:attr" or _local_name(tag) == "attr"


class TorznabResultsXmlParser:
    """
    XML parser for Torznab indexer results.

    See https://torznab.github.io/spec-1.3-draft/torznab/Specification-v1.3.html
    """

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, xml: str) -> list[Torrent]:
        root = ET.fromstring(xml)
        if _local_name(root.tag) != "rss":
            return []
        torrents: list[Torrent] = []
        for channel in root:
            if _local_name(channel.tag) != "channel":
                continue
            for item in channel:
                if _local_name(item.tag) != "item":
                    continue
                torrent = self._parse_item(item)
                if torrent is not None:
                    torrents.append(torrent)
        return torrents

    def _parse_item(self, item: ET.Element) -> Optional[Torrent]:
        torrent_name: Optional[str] = None
        size: Optional[str] = None
        seeders: Optional[str] = None
        peers: Optional[str] = None
        upload_date = None
        details_page_url: Optional[str] = None
        magnet_uri: Optional[str] = None
        info_hash: Optional[str] = None
        file_download_link: Optional[str] = None
        category_ids: set[int] = set()

        for child in item:
            name = _local_name(child.tag)
            if name == "title":
                torrent_name = (child.text or "").strip() or None
            elif name == "comments":
                details_page_url = (child.text or "").strip() or None
            elif name == "pubDate":
                raw = (child.text or "").strip()
                if raw:
                    upload_date = utils.parse_rfc1123(raw)
            elif name == "size":
                size_bytes = (child.text or "").strip()
                if size_bytes:
                    size = utils.format_bytes(size_bytes)
            elif name == "enclosure" and file_download_link is None:
                enclosure_url = self._read_enclosure(child)
                if enclosure_url is not None and not enclosure_url.startswith(
                    "magnet:?"
                ):
                    file_download_link = enclosure_url
            elif _is_torznab_attr(child.tag):
                attr_name = child.attrib.get("name")
                attr_value = child.attrib.get("value")
                if attr_value is None:
                    continue
                if attr_name == "seeders":
                    seeders = attr_value
                elif attr_name == "peers":
                    peers = attr_value
                elif attr_name == "magneturl":
                    magnet_uri = attr_value
                elif attr_name == "infohash":
                    info_hash = attr_value
                elif attr_name == "category":
                    try:
                        cat_id = int(attr_value)
                    except ValueError:
                        continue
                    if cat_id < CUSTOM_CATEGORY_RANGE_START:
                        category_ids.add(cat_id)
                elif attr_name == "size" and size is None:
                    size = utils.format_bytes(attr_value)

        if torrent_name is None:
            return None
        if info_hash is None or magnet_uri is None:
            return None

        torrent_id = utils.create_torrent_id(self._provider_id, info_hash)
        category = Category.OTHER
        if category_ids:
            category = TorznabCategoryMapper.get_category_from_id(max(category_ids))

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=_to_uint(seeders),
            peers=_to_uint(peers),
            provider_name=self._provider_name,
            upload_date=upload_date,
            category=category,
            details_page_url=details_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
        )

    @staticmethod
    def _read_enclosure(enclosure: ET.Element) -> Optional[str]:
        url = enclosure.attrib.get("url")
        mime = enclosure.attrib.get("type")
        if mime == MIME_TYPE_TORRENT:
            return url
        return None


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
