"""Private tracker providers backed by Torznab/Jackett/Prowlarr.

These appear in the builtin list and activate when TORRENTSEARCH_TORZNAB
includes a matching entry, or when dedicated env vars are set:

  TORRENTSEARCH_JACKETT_URL=http://127.0.0.1:9117
  TORRENTSEARCH_JACKETT_KEY=yourapikey

Optional per-indexer override:
  TORRENTSEARCH_MYANONAMOUSE_URL=...
  TORRENTSEARCH_MYANONAMOUSE_KEY=...
"""

from __future__ import annotations

import os
import re
from typing import Optional

from ..models import Category, SearchProviderSafety, TorznabConfig
from ..network import NetworkClient
from ..provider import SearchProvider
from .torznab import TorznabSearchProvider

_ALL_CATS = set(Category)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _jackett_indexer_url(indexer_id: str) -> Optional[str]:
    base = _env("TORRENTSEARCH_JACKETT_URL").rstrip("/")
    if not base:
        return None
    # Accept either root Jackett URL or a full torznab path already.
    if "torznab" in base.lower():
        return base
    return f"{base}/api/v2.0/indexers/{indexer_id}/results/torznab"


def _config_for(
    *,
    provider_id: str,
    display_name: str,
    jackett_indexer_id: str,
    env_prefix: str,
) -> Optional[TorznabConfig]:
    url = _env(f"TORRENTSEARCH_{env_prefix}_URL") or _jackett_indexer_url(jackett_indexer_id)
    key = _env(f"TORRENTSEARCH_{env_prefix}_KEY") or _env("TORRENTSEARCH_JACKETT_KEY")
    if not url or not key:
        # Also accept TORRENTSEARCH_TORZNAB entries matched by name
        raw = _env("TORRENTSEARCH_TORZNAB")
        for entry in raw.split(";"):
            parts = [p.strip() for p in entry.split("|")]
            if len(parts) < 3:
                continue
            name, u, k = parts[0], parts[1], parts[2]
            if _slug(name) in {_slug(display_name), _slug(provider_id), _slug(jackett_indexer_id)}:
                return TorznabConfig(
                    id=provider_id,
                    search_provider_name=display_name,
                    url=u,
                    api_key=k,
                    supported_categories=_ALL_CATS,
                )
        return None
    return TorznabConfig(
        id=provider_id,
        search_provider_name=display_name,
        url=url,
        api_key=key,
        supported_categories=_ALL_CATS,
    )


class _PrivateTorznab(TorznabSearchProvider):
    """Torznab provider that no-ops (empty) when credentials are missing."""

    safety = SearchProviderSafety.SAFE
    enabled_by_default = False
    supported_categories = _ALL_CATS

    jackett_indexer_id: str = ""
    env_prefix: str = ""

    def __init__(self, network: NetworkClient):
        cfg = _config_for(
            provider_id=self.id,
            display_name=self.name,
            jackett_indexer_id=self.jackett_indexer_id,
            env_prefix=self.env_prefix,
        )
        if cfg is None:
            # Placeholder config — search returns [] until configured.
            cfg = TorznabConfig(
                id=self.id,
                search_provider_name=self.name,
                url="http://127.0.0.1/disabled",
                api_key="",
                supported_categories=_ALL_CATS,
            )
            self._configured = False
        else:
            self._configured = True
        super().__init__(cfg, network)
        # Restore class-level identity (TorznabSearchProvider overwrites from config).
        self.id = type(self).id
        self.name = type(self).name
        self.url = cfg.url if self._configured else type(self).url

    async def search(self, query: str, category: Category):
        if not self._configured:
            return []
        return await super().search(query, category)


class MyAnonaMouse(_PrivateTorznab):
    id = "myanonamouse"
    name = "MyAnonaMouse"
    url = "https://www.myanonamouse.net"
    jackett_indexer_id = "myanonamouse"
    env_prefix = "MYANONAMOUSE"
    enabled_by_default = False


class IPTorrents(_PrivateTorznab):
    id = "iptorrents"
    name = "IPTorrents"
    url = "https://iptorrents.com"
    jackett_indexer_id = "iptorrents"
    env_prefix = "IPTORRENTS"
    enabled_by_default = False


class TorrentLeech(_PrivateTorznab):
    id = "torrentleech"
    name = "TorrentLeech"
    url = "https://www.torrentleech.org"
    jackett_indexer_id = "torrentleech"
    env_prefix = "TORRENTLEECH"
    enabled_by_default = False
