"""Magnetio/Cauldron-inspired result ranking: parse, validate, dedupe, score."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .models import Category, Torrent
from .utils import get_bytes, get_info_hash_from_magnet_uri

# S01E02, 1x02, - 173, [173], EP173, Episode 173, "173 (1080p)"
_EPISODE_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})"),
    re.compile(r"\b(\d{1,2})[xX](\d{1,3})\b"),
    re.compile(r"\b[Ee][Pp](?:isode)?\s*(\d{1,3})\b", re.I),
    re.compile(r"[-–—]\s*(\d{2,4})\s*(?:[\[\(]|$)"),
    re.compile(r"[\[\(]\s*(\d{2,4})\s*[\]\)]"),
    re.compile(r"\b(\d{2,4})\s*[\(\[]\s*(?:\d{3,4}p|HEVC|x26|AVC)", re.I),
    re.compile(r"\bEpisode\s+(\d{1,3})\b", re.I),
]

_SEASON_ONLY = re.compile(r"\b[Ss]eason\s*(\d{1,2})\b|\b[Ss](\d{1,2})\b(?![Ee\d])")

_RES = [
    (re.compile(r"\b2160p\b|\b4k\b|\buhd\b", re.I), 2160, 40),
    (re.compile(r"\b1080p\b", re.I), 1080, 30),
    (re.compile(r"\b720p\b", re.I), 720, 20),
    (re.compile(r"\b480p\b", re.I), 480, 8),
    (re.compile(r"\b360p\b", re.I), 360, 2),
]

_CODECS = [
    (re.compile(r"\b(x265|h\.?265|hevc|hev1|hvc1)\b", re.I), 12),
    (re.compile(r"\b(x264|h\.?264|avc)\b", re.I), 8),
    (re.compile(r"\bav1\b", re.I), 10),
]

_PREFERRED_GROUPS = {
    "subsplease": 25,
    "erai-raws": 22,
    "horriblesubs": 18,
    "ember": 16,
    "judas": 16,
    "yify": 14,
    "yts": 14,
    "rarbg": 12,
    "sparks": 10,
    "fgt": 10,
    "ntb": 10,
    "eztv": 8,
}

_JUNK = re.compile(
    r"\b(cam|hdcam|telesync|ts|tc|scr|screener|dvdscr|r5|workprint)\b",
    re.I,
)

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True)
class ReleaseInfo:
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    height: Optional[int] = None
    group: Optional[str] = None
    junk: bool = False


def parse_release(title: str) -> ReleaseInfo:
    season: Optional[int] = None
    episode: Optional[int] = None
    for pat in _EPISODE_PATTERNS:
        m = pat.search(title)
        if not m:
            continue
        groups = m.groups()
        if len(groups) >= 2 and groups[0] is not None and groups[1] is not None:
            try:
                season = int(groups[0])
                episode = int(groups[1])
            except ValueError:
                continue
        elif groups[0] is not None:
            try:
                episode = int(groups[0])
            except ValueError:
                continue
        break
    if season is None:
        sm = _SEASON_ONLY.search(title)
        if sm:
            raw = sm.group(1) or sm.group(2)
            if raw:
                try:
                    season = int(raw)
                except ValueError:
                    pass

    year = None
    ym = _YEAR.search(title)
    if ym:
        try:
            year = int(ym.group(0))
        except ValueError:
            pass

    height = None
    for pat, h, _ in _RES:
        if pat.search(title):
            height = h
            break

    group = None
    # Leading [Group] … (SubsPlease, Erai-Raws) or trailing -Group / [Group]
    gm = re.match(r"^\[([A-Za-z][A-Za-z0-9._\-]{1,24})\]", title.strip())
    if gm and not re.fullmatch(r"[0-9A-Fa-f]{6,}", gm.group(1)):
        group = gm.group(1).lower()
    else:
        gm = re.search(r"[\[\-]([A-Za-z][A-Za-z0-9._\-]{1,24})\]?\s*$", title)
        if gm and not re.fullmatch(r"[0-9A-Fa-f]{6,}", gm.group(1)):
            group = gm.group(1).lower().strip("[]")

    return ReleaseInfo(
        season=season,
        episode=episode,
        year=year,
        height=height,
        group=group,
        junk=bool(_JUNK.search(title)),
    )


def parse_query_episode(query: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (season, episode, bare_episode_hint) from a user query."""
    info = parse_release(query)
    bare: Optional[int] = None
    # Trailing number often means anime episode: "Black Clover 173"
    m = re.search(r"\b(\d{1,4})\s*$", query.strip())
    if m and info.episode is None:
        try:
            bare = int(m.group(1))
            if 1 <= bare <= 2000:
                info = ReleaseInfo(
                    season=info.season,
                    episode=bare,
                    year=info.year,
                    height=info.height,
                    group=info.group,
                    junk=info.junk,
                )
        except ValueError:
            pass
    return info.season, info.episode, bare


def _normalize_hash(torrent: Torrent) -> str:
    h = (torrent.info_hash or "").strip().lower()
    if h:
        return h
    mag = torrent.magnet()
    if mag:
        try:
            return get_info_hash_from_magnet_uri(mag).lower()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def dedupe_by_infohash(torrents: list[Torrent]) -> list[Torrent]:
    """Keep the best entry per infohash (highest seeders, then peers)."""
    best: dict[str, Torrent] = {}
    order: list[str] = []
    orphans: list[Torrent] = []

    def _seeders(t: Torrent) -> int:
        return t.seeders if t.seeders is not None else -1

    def _peers(t: Torrent) -> int:
        return t.peers if t.peers is not None else -1

    for t in torrents:
        key = _normalize_hash(t)
        if not key:
            orphans.append(t)
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = t
            order.append(key)
            continue
        if (_seeders(t), _peers(t), len(t.name)) > (
            _seeders(prev),
            _peers(prev),
            len(prev.name),
        ):
            best[key] = t
    return [best[k] for k in order] + orphans


def _title_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    stop = {"the", "a", "an", "and", "of", "in", "to", "for", "on", "with"}
    return [w for w in cleaned.split() if len(w) > 1 and w not in stop]


def title_match_score(query: str, title: str) -> float:
    """0..1 token overlap; bonus if query title tokens appear early."""
    q = _title_tokens(query)
    # Drop trailing episode / season tokens from query for title compare
    while q and q[-1].isdigit():
        q.pop()
    q = [w for w in q if not re.fullmatch(r"s\d{1,2}e\d{1,3}", w)]
    if not q:
        return 0.5
    name = title.lower()
    hits = sum(1 for w in q if w in name)
    ratio = hits / len(q)
    # Prefer titles that start with the query phrase
    head = " ".join(q[: min(3, len(q))])
    if head and name.startswith(head):
        ratio = min(1.0, ratio + 0.25)
    return ratio


def episode_matches(
    query: str,
    title: str,
    *,
    category: Optional[Category] = None,
) -> bool:
    """True if title episode is compatible with the query (when query implies one)."""
    qs, qe, _ = parse_query_episode(query)
    if qe is None and qs is None:
        return True
    info = parse_release(title)
    if qe is not None:
        if info.episode is None:
            # Anime often only has " - 173 "; already parsed. If still None, soft-pass
            # unless category is Series (stricter).
            if category is Category.SERIES:
                return False
            return True
        if info.episode != qe:
            return False
    if qs is not None and info.season is not None and info.season != qs:
        return False
    return True


def quality_score(torrent: Torrent, query: str) -> float:
    """Higher is better — used as primary sort for relevance ranking."""
    name = torrent.name
    info = parse_release(name)
    score = 0.0

    score += title_match_score(query, name) * 100.0
    score += (torrent.seeders or 0) * 1.5
    score += (torrent.peers or 0) * 0.15

    for pat, _h, pts in _RES:
        if pat.search(name):
            score += pts
            break
    for pat, pts in _CODECS:
        if pat.search(name):
            score += pts
            break

    if info.group:
        score += _PREFERRED_GROUPS.get(info.group, 4)

    if info.junk:
        score -= 40

    size = get_bytes(torrent.size) if torrent.size else 0
    # Mild preference for mid-size releases (avoid tiny cams / absurd packs)
    if 200 * 1024 * 1024 <= size <= 8 * 1024 * 1024 * 1024:
        score += 8
    elif size > 20 * 1024 * 1024 * 1024:
        score -= 5

    return score


def rank_torrents(
    torrents: list[Torrent],
    query: str,
    *,
    category: Optional[Category] = None,
    strict_episode: bool = True,
) -> list[Torrent]:
    """Dedupe → episode filter → quality score sort."""
    deduped = dedupe_by_infohash(torrents)
    if strict_episode and query.strip():
        matched = [
            t for t in deduped if episode_matches(query, t.name, category=category)
        ]
        # Fall back if filtering wiped everything (bad parse)
        if matched:
            deduped = matched
    scored = [(quality_score(t, query), i, t) for i, t in enumerate(deduped)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, _, t in scored]
