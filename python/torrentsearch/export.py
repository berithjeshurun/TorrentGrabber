"""Serialize search results to JSON / CSV / TSV / XML / magnets."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import MagnetAvailable, MagnetRequiresFetch, SearchResults, Torrent, TorrentDetails

EXPORT_FORMATS = ("json", "csv", "tsv", "xml", "magnets", "table")


def magnet_of(t: Torrent) -> Optional[str]:
    if isinstance(t.magnet_uri, MagnetAvailable):
        return t.magnet_uri.value
    return None


def fetch_url_of(t: Torrent) -> Optional[str]:
    if isinstance(t.magnet_uri, MagnetRequiresFetch):
        return t.magnet_uri.url
    return None


def torrent_to_dict(t: Torrent) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "size": t.size,
        "seeders": t.seeders,
        "peers": t.peers,
        "provider": t.provider_name,
        "upload_date": t.upload_date.isoformat() if t.upload_date else None,
        "category": t.category.value if t.category else None,
        "details_page_url": t.details_page_url,
        "file_download_link": t.file_download_link,
        "magnet": magnet_of(t),
        "magnet_fetch_url": fetch_url_of(t),
        "nsfw": t.is_nsfw,
        "dead": t.is_dead,
    }


def details_to_dict(d: TorrentDetails) -> dict[str, Any]:
    return {
        "info_hash": d.info_hash,
        "name": d.name,
        "size": d.size,
        "seeders": d.seeders,
        "peers": d.peers,
        "upload_date": d.upload_date.isoformat() if d.upload_date else None,
        "category": d.category.value if d.category else None,
        "uploader": d.uploader,
        "last_checked": d.last_checked.isoformat() if d.last_checked else None,
        "magnet": d.magnet_uri,
        "file_download_link": d.file_download_link,
        "description": d.description,
        "poster_url": d.poster_url,
        "screenshot_urls": list(d.screenshot_urls),
    }


def search_payload(
    results: SearchResults,
    *,
    query: str,
    category: str,
) -> dict[str, Any]:
    return {
        "query": query,
        "category": category,
        "count": len(results.torrents),
        "errors": [
            {
                "provider_id": e.provider_id,
                "provider": e.provider_name,
                "url": e.provider_url,
                "kind": e.kind,
                "message": str(e.cause),
            }
            for e in results.errors
        ],
        "results": [torrent_to_dict(t) for t in results.torrents],
    }


def _csv_rows(torrents: Iterable[Torrent]) -> list[dict[str, Any]]:
    rows = []
    for t in torrents:
        d = torrent_to_dict(t)
        rows.append(
            {
                "id": d["id"],
                "name": d["name"],
                "size": d["size"] or "",
                "seeders": d["seeders"] if d["seeders"] is not None else "",
                "peers": d["peers"] if d["peers"] is not None else "",
                "provider": d["provider"],
                "upload_date": d["upload_date"] or "",
                "category": d["category"] or "",
                "details_page_url": d["details_page_url"] or "",
                "file_download_link": d["file_download_link"] or "",
                "magnet": d["magnet"] or "",
                "magnet_fetch_url": d["magnet_fetch_url"] or "",
                "nsfw": d["nsfw"],
                "dead": d["dead"],
            }
        )
    return rows


def format_csv(torrents: list[Torrent], *, delimiter: str = ",") -> str:
    rows = _csv_rows(torrents)
    buf = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else [
        "id",
        "name",
        "size",
        "seeders",
        "peers",
        "provider",
        "upload_date",
        "category",
        "details_page_url",
        "file_download_link",
        "magnet",
        "magnet_fetch_url",
        "nsfw",
        "dead",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def format_xml(payload: dict[str, Any]) -> str:
    root = ET.Element("torrentsearch")
    root.set("query", str(payload.get("query") or ""))
    root.set("category", str(payload.get("category") or ""))
    root.set("count", str(payload.get("count") or 0))

    errors_el = ET.SubElement(root, "errors")
    for err in payload.get("errors") or []:
        e = ET.SubElement(errors_el, "error")
        for key in ("provider_id", "provider", "url", "kind", "message"):
            if key in err and err[key] is not None:
                e.set(key if key != "message" else "message", str(err[key]))

    results_el = ET.SubElement(root, "results")
    for item in payload.get("results") or []:
        t_el = ET.SubElement(results_el, "torrent")
        for key, value in item.items():
            if value is None:
                continue
            if isinstance(value, bool):
                t_el.set(key, "true" if value else "false")
            elif key in {"name", "magnet", "description"}:
                child = ET.SubElement(t_el, key)
                child.text = str(value)
            else:
                t_el.set(key, str(value))

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def format_magnets(torrents: list[Torrent]) -> str:
    lines = []
    for t in torrents:
        m = magnet_of(t)
        if m and m.startswith("magnet:"):
            lines.append(m)
    return "\n".join(lines) + ("\n" if lines else "")


def format_json(payload: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(payload, indent=indent, ensure_ascii=False) + "\n"


def render_export(
    results: SearchResults,
    fmt: str,
    *,
    query: str,
    category: str,
) -> str:
    fmt = fmt.lower()
    payload = search_payload(results, query=query, category=category)
    if fmt == "json":
        return format_json(payload)
    if fmt == "csv":
        return format_csv(results.torrents, delimiter=",")
    if fmt == "tsv":
        return format_csv(results.torrents, delimiter="\t")
    if fmt == "xml":
        return format_xml(payload)
    if fmt == "magnets":
        return format_magnets(results.torrents)
    raise ValueError(f"unsupported export format: {fmt}")


def infer_format_from_path(path: str | Path) -> Optional[str]:
    suffix = Path(path).suffix.lower().lstrip(".")
    mapping = {
        "json": "json",
        "csv": "csv",
        "tsv": "tsv",
        "xml": "xml",
        "txt": "magnets",
        "magnet": "magnets",
        "magnets": "magnets",
    }
    return mapping.get(suffix)


def dump_results(
    results: SearchResults,
    path: str | Path,
    fmt: Optional[str] = None,
    *,
    query: str,
    category: str,
) -> Path:
    out = Path(path)
    resolved_fmt = (fmt or infer_format_from_path(out) or "json").lower()
    if resolved_fmt == "table":
        resolved_fmt = "json"
    text = render_export(results, resolved_fmt, query=query, category=category)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
