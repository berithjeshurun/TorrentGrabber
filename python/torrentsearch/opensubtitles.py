"""OpenSubtitles.com REST search + download (Webtor-style external subs).

Env:
  TORRENTSEARCH_OPENSUBTITLES_KEY   — API key (required for live search)
  TORRENTSEARCH_OPENSUBTITLES_LANG  — default language codes, comma list (eng)
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

import httpx

_API = "https://api.opensubtitles.com/api/v1"
_UA = "TorrentGrabber v0.5.2"


def _api_key() -> str:
    return (os.environ.get("TORRENTSEARCH_OPENSUBTITLES_KEY") or "").strip()


def _default_langs() -> str:
    return (os.environ.get("TORRENTSEARCH_OPENSUBTITLES_LANG") or "en").strip()


def configured() -> bool:
    return bool(_api_key())


def _guess_query(title: str) -> str:
    # Strip release junk for a cleaner OpenSubtitles query.
    t = re.sub(r"[\[\(].*?[\]\)]", " ", title)
    t = re.sub(
        r"\b(1080p|720p|2160p|480p|x264|x265|hevc|web-?dl|bluray|bdrip|hdtv|aac|opus)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s+", " ", t).strip(" -._")
    return t[:120]


async def search_subtitles(
    *,
    query: str,
    languages: Optional[str] = None,
    imdb_id: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    limit: int = 20,
) -> dict[str, Any]:
    key = _api_key()
    if not key:
        return {
            "configured": False,
            "results": [],
            "error": "Set TORRENTSEARCH_OPENSUBTITLES_KEY to enable OpenSubtitles",
        }

    params: dict[str, Any] = {
        "query": _guess_query(query) or query,
        "languages": languages or _default_langs(),
        "order_by": "download_count",
        "order_direction": "desc",
    }
    if imdb_id:
        params["imdb_id"] = imdb_id.lstrip("tt")
    if season is not None:
        params["season_number"] = season
    if episode is not None:
        params["episode_number"] = episode

    headers = {
        "Api-Key": key,
        "User-Agent": _UA,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.get(f"{_API}/subtitles", params=params, headers=headers)
        if r.status_code >= 400:
            return {
                "configured": True,
                "results": [],
                "error": f"OpenSubtitles HTTP {r.status_code}: {r.text[:200]}",
            }
        data = r.json()

    out: list[dict[str, Any]] = []
    for item in (data.get("data") or [])[:limit]:
        attrs = item.get("attributes") or {}
        files = attrs.get("files") or []
        file0 = files[0] if files else {}
        out.append(
            {
                "id": item.get("id"),
                "file_id": file0.get("file_id"),
                "release": attrs.get("release") or attrs.get("feature_details", {}).get("title"),
                "language": attrs.get("language"),
                "hearing_impaired": bool(attrs.get("hearing_impaired")),
                "download_count": attrs.get("download_count"),
                "fps": attrs.get("fps"),
                "url": attrs.get("url"),
            }
        )
    return {"configured": True, "results": out, "query": params.get("query")}


async def download_subtitle(file_id: int) -> dict[str, Any]:
    """Return {content, file_name, encoding} for a subtitle file_id."""
    key = _api_key()
    if not key:
        raise RuntimeError("TORRENTSEARCH_OPENSUBTITLES_KEY not set")

    headers = {
        "Api-Key": key,
        "User-Agent": _UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{_API}/download",
            headers=headers,
            json={"file_id": int(file_id)},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenSubtitles download {r.status_code}: {r.text[:240]}")
        meta = r.json()
        link = meta.get("link")
        if not link:
            raise RuntimeError("OpenSubtitles download missing link")
        body = await client.get(link, headers={"User-Agent": _UA})
        body.raise_for_status()
        text = body.content
        # Prefer UTF-8; fall back latin-1 for older SRT
        try:
            content = text.decode("utf-8")
        except UnicodeDecodeError:
            content = text.decode("latin-1", errors="replace")
        return {
            "content": content,
            "file_name": meta.get("file_name") or f"{file_id}.srt",
            "remaining": meta.get("remaining"),
        }


def srt_to_vtt(srt: str) -> str:
    """Minimal SRT→WebVTT conversion for the cinema player."""
    text = srt.replace("\r\n", "\n").replace("\r", "\n").strip()
    if text.upper().startswith("WEBVTT"):
        return text if text.endswith("\n") else text + "\n"
    # Drop numeric cue indices; rewrite timestamps comma → dot
    lines: list[str] = ["WEBVTT", ""]
    for block in re.split(r"\n\s*\n", text):
        blines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not blines:
            continue
        if re.fullmatch(r"\d+", blines[0].strip()):
            blines = blines[1:]
        if not blines:
            continue
        # timestamp line
        if "-->" in blines[0]:
            blines[0] = blines[0].replace(",", ".")
        lines.extend(blines)
        lines.append("")
    return "\n".join(lines)
