"""Launch optional external players (mpv / VLC) against engine media URLs."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _which_env(env_key: str, *names: str) -> Optional[Path]:
    env = (os.environ.get(env_key) or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def find_mpv() -> Optional[Path]:
    return _which_env("TORRENTSEARCH_MPV", "mpv", "mpv.exe")


def find_vlc() -> Optional[Path]:
    return _which_env(
        "TORRENTSEARCH_VLC",
        "vlc",
        "vlc.exe",
    )


def open_player(player: str, media_url: str, *, title: Optional[str] = None) -> dict:
    """Spawn mpv or VLC pointed at an HTTP Range / HLS URL."""
    kind = (player or "mpv").strip().lower()
    if kind in ("mpv", "auto"):
        exe = find_mpv()
        if exe is None and kind == "auto":
            exe = find_vlc()
            kind = "vlc" if exe else "mpv"
        if exe is None:
            raise FileNotFoundError(
                "mpv not found — install it or set TORRENTSEARCH_MPV"
            )
        args = [str(exe), "--force-window=yes", media_url]
        if title:
            args.insert(1, f"--title={title}")
    elif kind == "vlc":
        exe = find_vlc()
        if exe is None:
            raise FileNotFoundError(
                "VLC not found — install it or set TORRENTSEARCH_VLC"
            )
        args = [str(exe), media_url]
        if title:
            args.extend([f"--meta-title={title}"])
    else:
        raise ValueError(f"unknown player '{player}' (use mpv or vlc)")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return {
        "player": kind,
        "exe": str(exe),
        "pid": proc.pid,
        "url": media_url,
    }
