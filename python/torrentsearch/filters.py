"""Client-side torrent filters (Android TorrentFilters)."""

from __future__ import annotations

from typing import Callable

from .models import Category, Torrent

TorrentFilter = Callable[[Torrent], bool]


def is_sfw() -> TorrentFilter:
    return lambda t: not t.is_nsfw


def is_alive() -> TorrentFilter:
    return lambda t: not t.is_dead


def not_excluded_provider(excluded: set[str]) -> TorrentFilter:
    return lambda t: t.provider_name not in excluded


def matches_category(category: Category) -> TorrentFilter:
    return lambda t: t.category is category


def matches_query(query: str) -> TorrentFilter:
    words = [w for w in query.split() if w]

    def _f(t: Torrent) -> bool:
        name = t.name.lower()
        return any(w.lower() in name for w in words)

    return _f


def apply_filters(torrents: list[Torrent], *filters: TorrentFilter) -> list[Torrent]:
    out = torrents
    for f in filters:
        out = [t for t in out if f(t)]
    return out


def parse_filter_keywords(raw: str | None) -> list[str]:
    """Split a comma-separated filter string into non-empty keywords."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def boost_keyword_matches(
    torrents: list[Torrent], keywords: list[str]
) -> list[Torrent]:
    """Stable-rank titles that contain filter keywords to the top.

    More matching keywords → higher. Relative order among equal scores is kept.
    """
    kws = [k.lower() for k in keywords if k]
    if not kws:
        return torrents

    def score(t: Torrent) -> int:
        name = t.name.lower()
        return sum(1 for k in kws if k in name)

    indexed = list(enumerate(torrents))
    indexed.sort(key=lambda pair: (-score(pair[1]), pair[0]))
    return [t for _, t in indexed]
