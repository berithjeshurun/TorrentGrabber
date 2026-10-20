"""Locate bundled ffmpeg/ffprobe under python/etc and probe media tracks."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# python/torrentsearch/downloader/this → python/etc
_ETC = Path(__file__).resolve().parents[2] / "etc"


def _bin(name: str) -> Optional[Path]:
    suffix = ".exe" if sys.platform == "win32" else ""
    local = _ETC / f"{name}{suffix}"
    if local.is_file():
        return local
    which = shutil.which(name)
    return Path(which) if which else None


def ffmpeg_path() -> Optional[Path]:
    return _bin("ffmpeg")


def ffprobe_path() -> Optional[Path]:
    return _bin("ffprobe")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None and ffprobe_path() is not None


@dataclass
class MediaTrack:
    index: int
    codec_type: str  # video | audio | subtitle
    codec_name: str
    language: Optional[str] = None
    title: Optional[str] = None
    channels: Optional[int] = None
    sample_rate: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    default: bool = False
    forced: bool = False

    def label(self) -> str:
        bits = []
        if self.language:
            bits.append(self.language)
        if self.title:
            bits.append(self.title)
        if self.codec_name:
            bits.append(self.codec_name)
        if self.channels:
            bits.append(f"{self.channels}ch")
        if self.width and self.height:
            bits.append(f"{self.width}x{self.height}")
        if self.default:
            bits.append("default")
        if self.forced:
            bits.append("forced")
        return " · ".join(bits) if bits else f"#{self.index}"

    def text_based(self) -> bool:
        c = (self.codec_name or "").lower()
        bitmap = {
            "hdmv_pgs_subtitle",
            "dvd_subtitle",
            "dvdsub",
            "pgssub",
            "xsub",
            "dvb_subtitle",
        }
        return c not in bitmap

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": self.codec_type,
            "codec": self.codec_name,
            "language": self.language,
            "title": self.title,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "width": self.width,
            "height": self.height,
            "default": self.default,
            "forced": self.forced,
            "label": self.label(),
            "text_based": self.text_based() if self.codec_type == "subtitle" else None,
        }


@dataclass
class ProbeResult:
    format_name: str
    duration: Optional[float]
    tracks: list[MediaTrack]
    file_name: Optional[str] = None

    @property
    def video(self) -> list[MediaTrack]:
        return [t for t in self.tracks if t.codec_type == "video"]

    @property
    def audio(self) -> list[MediaTrack]:
        return [t for t in self.tracks if t.codec_type == "audio"]

    @property
    def subtitles(self) -> list[MediaTrack]:
        return [t for t in self.tracks if t.codec_type == "subtitle"]

    def needs_hls(self) -> bool:
        """Browser <video> is weak on MKV / multi-track — prefer ffmpeg HLS."""
        fmt = (self.format_name or "").lower()
        if any(x in fmt for x in ("matroska", "mkv", "avi", "mpegts", "asf")):
            return True
        if len(self.audio) > 1 or len(self.subtitles) > 0:
            return True
        # Non-H.264/AV1/VP9/AVC in mp4 can still fail; keep simple for now.
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format_name,
            "duration": self.duration,
            "file_name": self.file_name,
            "needs_hls": self.needs_hls(),
            "ffmpeg": ffmpeg_available(),
            "video": [t.to_dict() for t in self.video],
            "audio": [t.to_dict() for t in self.audio],
            "subtitles": [t.to_dict() for t in self.subtitles],
        }


def _tags(stream: dict) -> dict:
    return stream.get("tags") or {}


def _parse_tracks(data: dict) -> ProbeResult:
    fmt = data.get("format") or {}
    duration = None
    try:
        if fmt.get("duration") is not None:
            duration = float(fmt["duration"])
    except (TypeError, ValueError):
        duration = None

    tracks: list[MediaTrack] = []
    for s in data.get("streams") or []:
        disp = s.get("disposition") or {}
        tags = _tags(s)
        tracks.append(
            MediaTrack(
                index=int(s.get("index", 0)),
                codec_type=str(s.get("codec_type") or "unknown"),
                codec_name=str(s.get("codec_name") or "unknown"),
                language=tags.get("language") or tags.get("LANGUAGE"),
                title=tags.get("title") or tags.get("TITLE"),
                channels=s.get("channels"),
                sample_rate=str(s["sample_rate"]) if s.get("sample_rate") else None,
                width=s.get("width"),
                height=s.get("height"),
                default=bool(disp.get("default")),
                forced=bool(disp.get("forced")),
            )
        )

    return ProbeResult(
        format_name=str(fmt.get("format_name") or ""),
        duration=duration,
        tracks=tracks,
    )


async def probe_url(media_url: str, *, timeout: float = 45.0) -> ProbeResult:
    probe = ffprobe_path()
    if not probe:
        raise RuntimeError("ffprobe not found (expected under python/etc)")

    cmd = [
        str(probe),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-probesize",
        "5M",
        "-analyzeduration",
        "10M",
        media_url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffprobe timed out — file may still be buffering") from None

    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace")[-600:]
        raise RuntimeError(f"ffprobe failed: {detail or 'unknown error'}")

    data = json.loads(out.decode("utf-8"))
    return _parse_tracks(data)
