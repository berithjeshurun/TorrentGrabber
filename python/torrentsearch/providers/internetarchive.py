"""Internet Archive search provider — port of InternetArchive.kt."""

from __future__ import annotations

from typing import Any, Optional

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


def _category_from_media_type(media_type: str) -> Category:
    return {
        "software": Category.APPS,
        "texts": Category.BOOKS,
        "movies": Category.MOVIES,
    }.get(media_type, Category.OTHER)


class InternetArchive(SearchProvider, TorrentDetailsProvider):
    id = "internetarchive"
    name = "InternetArchive"
    url = "https://archive.org"
    supported_categories = {
        Category.APPS,
        Category.BOOKS,
        Category.MOVIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _IAResultsJsonParser(self.id, self.name, self.url)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = (
            f"{self.url}/advancedsearch.php"
            f"?q=title:{query}"
            f"{self._category_query(category)}"
            f"&fl[]=title,item_size,publicdate,mediatype,identifier,btih"
            f"&rows=100"
            f"&page=1"
            f"&output=json"
        )
        response_json = await self.network.get_json(request_url)
        if response_json is None or not isinstance(response_json, dict):
            return []
        return self._results_parser.parse(response_json) or []

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        identifier = details_page_url.rsplit("/", 1)[-1]
        json_metadata_page_url = f"https://archive.org/metadata/{identifier}"
        data = await self.network.get_json(json_metadata_page_url)
        if data is None or not isinstance(data, dict):
            return None
        return _IAMetadataJsonParser.parse(data)

    @staticmethod
    def _category_query(category: Category) -> str:
        if category is Category.ALL:
            return ""
        if category in (
            Category.ANIME,
            Category.GAMES,
            Category.MUSIC,
            Category.PORN,
            Category.SERIES,
            Category.OTHER,
        ):
            media = "other"
        elif category is Category.APPS:
            media = "software"
        elif category is Category.BOOKS:
            media = "texts"
        elif category is Category.MOVIES:
            media = "movies"
        else:
            media = "other"
        return f"%20AND%20mediatype:%28{media}%29"


class _IAResultsJsonParser:
    def __init__(self, provider_id: str, provider_name: str, provider_url: str):
        self._provider_id = provider_id
        self._provider_name = provider_name
        self._provider_url = provider_url

    def parse(self, json_obj: dict[str, Any]) -> Optional[list[Torrent]]:
        response = json_obj.get("response")
        if not isinstance(response, dict):
            return None
        docs = response.get("docs")
        if not isinstance(docs, list):
            return None
        results: list[Torrent] = []
        for doc in docs:
            if isinstance(doc, dict):
                torrent = self._parse_doc_object(doc)
                if torrent is not None:
                    results.append(torrent)
        return results

    def _parse_doc_object(self, obj: dict[str, Any]) -> Optional[Torrent]:
        name = obj.get("title")
        if not isinstance(name, str):
            return None
        info_hash_raw = obj.get("btih")
        if not isinstance(info_hash_raw, str):
            return None
        info_hash = info_hash_raw.lower().strip()
        magnet_uri = utils.create_magnet_uri(info_hash)

        torrent_remote_id = obj.get("identifier")
        source_id = (
            torrent_remote_id
            if isinstance(torrent_remote_id, str)
            else info_hash
        )
        torrent_id = utils.create_torrent_id(self._provider_id, source_id)

        size = None
        item_size = obj.get("item_size")
        if item_size is not None:
            try:
                size = utils.format_bytes(float(item_size))
            except (TypeError, ValueError):
                size = None

        upload_date = None
        publicdate = obj.get("publicdate")
        if isinstance(publicdate, str):
            upload_date = utils.parse_iso(publicdate)

        category = None
        mediatype = obj.get("mediatype")
        if isinstance(mediatype, str):
            category = _category_from_media_type(mediatype)

        description_page_url = None
        if isinstance(torrent_remote_id, str):
            description_page_url = f"{self._provider_url}/details/{torrent_remote_id}"

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            upload_date=upload_date,
            category=category,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            details_page_url=description_page_url,
        )


class _IAMetadataJsonParser:
    @classmethod
    def parse(cls, json_obj: dict[str, Any]) -> Optional[TorrentDetails]:
        last_checked = None
        item_last_updated = json_obj.get("item_last_updated")
        if item_last_updated is not None:
            try:
                last_checked = utils.epoch_second_to_datetime(int(item_last_updated))
            except (TypeError, ValueError):
                last_checked = None

        size = None
        item_size = json_obj.get("item_size")
        if item_size is not None:
            try:
                size = utils.format_bytes(float(item_size))
            except (TypeError, ValueError):
                size = None

        metadata_obj = json_obj.get("metadata")
        if not isinstance(metadata_obj, dict):
            return None
        name = metadata_obj.get("title")
        if not isinstance(name, str):
            return None

        upload_date = None
        publicdate = metadata_obj.get("publicdate")
        if isinstance(publicdate, str):
            upload_date = utils.parse_date(publicdate, "yyyy-MM-dd HH:mm:ss")

        uploader = metadata_obj.get("uploader")
        if not isinstance(uploader, str):
            uploader = None
        description = metadata_obj.get("description")
        if not isinstance(description, str):
            description = None

        # 1:1 with Kotlin — uses key "metadata" (likely a bug vs "mediatype")
        category = None
        meta_type = metadata_obj.get("metadata")
        if isinstance(meta_type, str):
            category = _category_from_media_type(meta_type)

        files = json_obj.get("files")
        if not isinstance(files, list):
            return None
        torrent_obj = None
        for f in files:
            if isinstance(f, dict) and "btih" in f:
                torrent_obj = f
                break
        if torrent_obj is None:
            return None

        identifier = metadata_obj.get("identifier")
        if not isinstance(identifier, str):
            return None
        info_hash = torrent_obj.get("btih")
        if not isinstance(info_hash, str):
            return None
        torrent_file_name = torrent_obj.get("name")
        if not isinstance(torrent_file_name, str):
            return None
        file_download_link = f"https://archive.org/download/{identifier}/{torrent_file_name}"

        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            upload_date=upload_date,
            category=category,
            uploader=uploader,
            last_checked=last_checked,
            magnet_uri=utils.create_magnet_uri(info_hash),
            file_download_link=file_download_link,
            description=description,
        )
