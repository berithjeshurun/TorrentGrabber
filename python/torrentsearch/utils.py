"""Utilities mirrored from Android TorrentUtils / FileSizeUtils / SortUtils / TorrentDateParser."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import unquote

from .models import SortCriteria, SortOrder, Torrent

PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonoid.ch:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker2.dler.org:80/announce",
    "udp://tracker.qu.ax:6969/announce",
    "udp://tracker.filemail.com:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://tracker.0x7c0.com:6969/announce",
    "udp://tracker-udp.gbitt.info:80/announce",
    "udp://run.publictracker.xyz:6969/announce",
    "udp://retracker01-msk-virt.corbina.net:80/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://opentracker.io:6969/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://leet-tracker.moe:1337/announce",
]

_PUBLIC_TRACKERS_PARAMS = "&".join(f"tr={t}" for t in PUBLIC_TRACKERS)
_MAGNET_PREFIX = "magnet:?xt=urn:btih:"

KB = 1024.0
MB = KB * 1024.0
GB = MB * 1024.0
TB = GB * 1024.0
PB = TB * 1024.0

_RELATIVE_TIME = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
    r"h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks|mo|mos|month|months|"
    r"y|yr|yrs|year|years)\b\s*(ago)?\b",
    re.IGNORECASE,
)

_RUSSIAN_MONTHS = {
    "Янв": "Jan",
    "Фев": "Feb",
    "Мар": "Mar",
    "Апр": "Apr",
    "Май": "May",
    "Июн": "Jun",
    "Июл": "Jul",
    "Авг": "Aug",
    "Сен": "Sep",
    "Окт": "Oct",
    "Ноя": "Nov",
    "Дек": "Dec",
}

# Java/Kotlin SimpleDateFormat → Python strptime
_FORMAT_MAP = {
    "d/M/yyyy": "%d/%m/%Y",
    "dd/MM/yyyy": "%d/%m/%Y",
    "dd/MM/yyyy HH:mm": "%d/%m/%Y %H:%M",
    "dd-MM-yyyy": "%d-%m-%Y",
    "dd-MM-yyyy H:m:s": "%d-%m-%Y %H:%M:%S",
    "MMM d yy": "%b %d %y",
    "MMM d, yyyy, h:mm a": "%b %d, %Y, %I:%M %p",
    "h a MMM d yyyy": "%I %p %b %d %Y",
    "yyyy-MM-dd HH:mm z": "%Y-%m-%d %H:%M %Z",
    "yyyy-MM-dd hh:mm z": "%Y-%m-%d %I:%M %Z",
    "yyyy-MM-dd HH:mm:ssxxx": "%Y-%m-%d %H:%M:%S%z",
    "d MMM yy": "%d %b %y",
    "dd MMM yy": "%d %b %y",
    "d MMM yyyy": "%d %b %Y",
    "dd MMM yyyy": "%d %b %Y",
    "d MMMM yyyy": "%d %B %Y",
    "dd MMMM yyyy": "%d %B %Y",
    "dd MMM yyyy HH:mm:ss": "%d %b %Y %H:%M:%S",
    "MMM d, yyyy": "%b %d, %Y",
    "MMM d, yyyy HH:mm": "%b %d, %Y %H:%M",
    "M/d/yyyy": "%m/%d/%Y",
    "yyyy-MM-dd": "%Y-%m-%d",
    "yyyy-MM-dd HH:mm": "%Y-%m-%d %H:%M",
    "yyyy-MM-dd HH:mm:ss": "%Y-%m-%d %H:%M:%S",
    "uuuu-MM-dd HH:mm:ss": "%Y-%m-%d %H:%M:%S",
    "E MMM dd yyyy HH:mm:ss": "%a %b %d %Y %H:%M:%S",
    "yyyy/MM/dd": "%Y/%m/%d",
    "yyyy/MM/dd HH:mm": "%Y/%m/%d %H:%M",
    "h:m a": "%I:%M %p",
}


def create_torrent_id(provider_id: str, source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode()).hexdigest()
    return f"{provider_id}:{digest}"


def create_magnet_uri(info_hash: str) -> str:
    return f"{_MAGNET_PREFIX}{info_hash}&{_PUBLIC_TRACKERS_PARAMS}"


def get_info_hash_from_magnet_uri(magnet_uri: str) -> str:
    if not magnet_uri.startswith(_MAGNET_PREFIX):
        raise ValueError(f"Can't extract info hash from '{magnet_uri}'")
    rest = magnet_uri[len(_MAGNET_PREFIX) :]
    return rest.split("&", 1)[0].lower().strip()


def normalize_size(size: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(size):
        out.append(ch)
        if i < len(size) - 1 and ch.isdigit() and size[i + 1].isalpha():
            out.append(" ")
    return "".join(out)


def format_bytes(bytes_value: float | str) -> str:
    b = float(bytes_value)
    if b >= PB:
        value, unit = b / PB, "PB"
    elif b >= TB:
        value, unit = b / TB, "TB"
    elif b >= GB:
        value, unit = b / GB, "GB"
    elif b >= MB:
        value, unit = b / MB, "MB"
    elif b >= KB:
        value, unit = b / KB, "KB"
    else:
        value, unit = 0.0, "B"
    return f"{value:.2f} {unit}"


def get_bytes(formatted_size: str) -> float:
    value_s, unit = _size_value_and_unit(formatted_size)
    try:
        value = float(value_s)
    except ValueError:
        return 0.0
    unit = unit.upper() if unit.lower() not in {"kib", "mib", "gib", "tib", "pib"} else unit
    mapping = {
        "PB": PB,
        "PIB": PB,
        "TB": TB,
        "TIB": TB,
        "GB": GB,
        "GIB": GB,
        "MB": MB,
        "MIB": MB,
        "KB": KB,
        "KIB": KB,
    }
    return value * mapping.get(unit.upper(), 0.0)


def _size_value_and_unit(formatted_size: str) -> tuple[str, str]:
    if " " in formatted_size:
        value, unit = formatted_size.split(" ", 1)
        return value, unit
    unit_start = next((i for i, c in enumerate(formatted_size) if c.isalpha()), None)
    if unit_start is None:
        return formatted_size, "KB"
    return formatted_size[:unit_start], formatted_size[unit_start:]


def epoch_second_to_datetime(second: int | float) -> datetime:
    return datetime.fromtimestamp(int(second), tz=timezone.utc)


def parse_iso(date: str) -> datetime:
    return datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_rfc1123(date: str) -> datetime:
    # e.g. "Mon, 02 Jan 2006 15:04:05 GMT"
    return datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)


def parse_date(date: str, fmt: str) -> Optional[datetime]:
    py_fmt = _FORMAT_MAP.get(fmt, fmt)
    candidate = date
    # Java xxx offset (+00:00) → Python %z (+0000)
    if "%z" in py_fmt and len(candidate) >= 6 and candidate[-3] == ":":
        candidate = candidate[:-3] + candidate[-2:]
    try:
        dt = datetime.strptime(candidate, py_fmt)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        # date-only fallbacks already covered by LocalDate in Kotlin
        try:
            # try without time if format has time
            date_only = py_fmt.split(" ")[0]
            dt = datetime.strptime(date, date_only)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def get_today_date() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def get_yesterday_date() -> datetime:
    return get_today_date() - timedelta(days=1)


def get_current_year() -> int:
    return datetime.now(timezone.utc).year


def try_parse_time(time: str) -> Optional[datetime]:
    """Parse a time-of-day (h:m a) as today UTC — mirrors TorrentDateParser.tryParseTime."""
    parsed = parse_date(time, "h:m a")
    if parsed is None:
        return None
    today = get_today_date()
    return today.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)


def try_parse_special_relative(date: str) -> Optional[datetime]:
    key = date.lower()
    now = datetime.now(timezone.utc)
    if key in {"today", "just now", "moments ago"}:
        return now
    if key == "yesterday":
        return get_yesterday_date()
    if key == "last week":
        return now - timedelta(days=7)
    if key == "last month":
        return now - timedelta(days=30)
    if key == "last year":
        return now - timedelta(days=365)
    return None


def try_parse_relative(date: str) -> Optional[datetime]:
    cleaned = date.removesuffix("+").strip()
    special = try_parse_special_relative(cleaned)
    if special is not None:
        return special
    matched = _RELATIVE_TIME.search(cleaned)
    if not matched:
        return None
    value = int(float(matched.group(1)))
    unit = matched.group(2).lower()
    if unit in {"s", "sec", "secs", "second", "seconds"}:
        delta = timedelta(seconds=value)
    elif unit in {"m", "min", "mins", "minute", "minutes"}:
        delta = timedelta(minutes=value)
    elif unit in {"h", "hr", "hrs", "hour", "hours"}:
        delta = timedelta(hours=value)
    elif unit in {"d", "day", "days"}:
        delta = timedelta(days=value)
    elif unit in {"w", "wk", "wks", "week", "weeks"}:
        delta = timedelta(days=value * 7)
    elif unit in {"mo", "mos", "month", "months"}:
        delta = timedelta(days=value * 30)
    elif unit in {"y", "yr", "yrs", "year", "years"}:
        delta = timedelta(days=value * 365)
    else:
        return None
    return datetime.now(timezone.utc) - delta


def convert_russian_month_to_english(date: str) -> str:
    day, russian_month, year = date.split(" ", 2)
    return f"{day} {_RUSSIAN_MONTHS[russian_month]} {year}"


def parse_size_from_magnet_uri(magnet_uri: str) -> Optional[str]:
    # magnet:?xt=...&xl=12345&...
    query = magnet_uri.split("?", 1)[-1] if "?" in magnet_uri else magnet_uri
    for part in query.split("&"):
        if part.startswith("xl="):
            raw = unquote(part[3:])
            try:
                return format_bytes(float(raw))
            except ValueError:
                return None
    return None


def create_sort_key(
    criteria: SortCriteria, order: SortOrder
) -> Callable[[Torrent], tuple]:
    reverse = order is SortOrder.DESCENDING

    def key(t: Torrent):
        if criteria is SortCriteria.NAME:
            val: object = t.name.lower()
        elif criteria is SortCriteria.SEEDERS:
            val = t.seeders if t.seeders is not None else -1
        elif criteria is SortCriteria.PEERS:
            val = t.peers if t.peers is not None else -1
        elif criteria is SortCriteria.FILE_SIZE:
            val = get_bytes(t.size) if t.size else -1.0
        elif criteria is SortCriteria.DATE:
            val = t.upload_date.timestamp() if t.upload_date else -1.0
        else:
            val = 0
        return (val,)

    # callers use sorted(..., key=..., reverse=reverse)
    key.reverse = reverse  # type: ignore[attr-defined]
    return key


def sort_torrents(
    torrents: list[Torrent],
    criteria: SortCriteria = SortCriteria.SEEDERS,
    order: SortOrder = SortOrder.DESCENDING,
) -> list[Torrent]:
    reverse = order is SortOrder.DESCENDING
    key = create_sort_key(criteria, order)
    return sorted(torrents, key=key, reverse=reverse)
