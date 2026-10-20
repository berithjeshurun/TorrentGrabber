"""Plain-language activity / health feed (not technical debug logs)."""

from __future__ import annotations

import asyncio
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Optional


class ActivityLevel(str, Enum):
    INFO = "info"
    GOOD = "good"
    WARN = "warn"
    BAD = "bad"


@dataclass(frozen=True)
class ActivityEvent:
    time: datetime
    level: ActivityLevel
    message: str
    area: str = "system"  # system | search | download | network

    def as_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "level": self.level.value,
            "area": self.area,
            "message": self.message,
        }


@dataclass
class HealthSnapshot:
    ok: bool
    phrase: str
    searching: int = 0
    downloads_active: int = 0
    downloads_streaming: int = 0
    underruns: int = 0
    last_error: Optional[str] = None
    uptime_seconds: float = 0.0
    recent: list[ActivityEvent] = field(default_factory=list)


class ActivityLog:
    """
    Ring buffer of human-readable status lines.

    Example messages:
      "Looking up 'ubuntu' across 9 sites…"
      "Found 42 torrents; 1 site had trouble"
      "Download started in stream mode"
      "Playback buffer looking thin — catching up"
    """

    def __init__(self, *, capacity: int = 200, echo: bool = True):
        self._events: Deque[ActivityEvent] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()
        self._echo = echo
        self._started_at = datetime.now(timezone.utc)
        self._searching = 0
        self._downloads_active = 0
        self._downloads_streaming = 0
        self._underruns = 0
        self._last_error: Optional[str] = None
        self._manager_running = False

    @property
    def manager_running(self) -> bool:
        return self._manager_running

    def set_manager_running(self, running: bool) -> None:
        self._manager_running = running

    def update_download_counts(
        self,
        *,
        active: int,
        streaming: int = 0,
        underruns: int = 0,
    ) -> None:
        self._downloads_active = active
        self._downloads_streaming = streaming
        self._underruns = underruns

    async def say(
        self,
        message: str,
        *,
        level: ActivityLevel = ActivityLevel.INFO,
        area: str = "system",
    ) -> ActivityEvent:
        event = ActivityEvent(
            time=datetime.now(timezone.utc),
            level=level,
            message=message.strip(),
            area=area,
        )
        async with self._lock:
            self._events.append(event)
            if level is ActivityLevel.BAD:
                self._last_error = event.message
        if self._echo:
            self._print(event)
        return event

    def say_nowait(
        self,
        message: str,
        *,
        level: ActivityLevel = ActivityLevel.INFO,
        area: str = "system",
    ) -> None:
        """Fire-and-forget from sync or async contexts (schedules if loop running)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            event = ActivityEvent(
                time=datetime.now(timezone.utc),
                level=level,
                message=message.strip(),
                area=area,
            )
            self._events.append(event)
            if level is ActivityLevel.BAD:
                self._last_error = event.message
            if self._echo:
                self._print(event)
            return
        loop.create_task(self.say(message, level=level, area=area))

    async def begin_search(self) -> None:
        async with self._lock:
            self._searching += 1

    async def end_search(self) -> None:
        async with self._lock:
            self._searching = max(0, self._searching - 1)

    def recent(self, limit: int = 40) -> list[ActivityEvent]:
        items = list(self._events)
        if limit <= 0:
            return items
        return items[-limit:]

    def snapshot(self, *, recent_limit: int = 12) -> HealthSnapshot:
        uptime = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        if not self._manager_running:
            phrase = "Downloader is not running yet"
            ok = False
        elif self._underruns > 0:
            phrase = "Streaming, but a buffer is catching up"
            ok = True
        elif self._searching > 0 and self._downloads_active > 0:
            phrase = "Searching and downloading — everything is moving"
            ok = True
        elif self._searching > 0:
            phrase = "Searching sites right now"
            ok = True
        elif self._downloads_streaming > 0:
            phrase = "Streaming looks steady"
            ok = True
        elif self._downloads_active > 0:
            phrase = "Downloads are in progress"
            ok = True
        else:
            phrase = "Quiet and ready — nothing stuck"
            ok = True

        return HealthSnapshot(
            ok=ok,
            phrase=phrase,
            searching=self._searching,
            downloads_active=self._downloads_active,
            downloads_streaming=self._downloads_streaming,
            underruns=self._underruns,
            last_error=self._last_error,
            uptime_seconds=uptime,
            recent=self.recent(recent_limit),
        )

    def _print(self, event: ActivityEvent) -> None:
        stamp = event.time.strftime("%H:%M:%S")
        mark = {
            ActivityLevel.INFO: "·",
            ActivityLevel.GOOD: "✓",
            ActivityLevel.WARN: "!",
            ActivityLevel.BAD: "✗",
        }.get(event.level, "·")
        line = f"[{stamp}] {mark} {event.message}\n"
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except Exception:
            pass


# Process-wide feed used by API + downloader.
activity = ActivityLog()
