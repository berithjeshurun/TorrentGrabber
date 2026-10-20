"""Optional webtorrent-cli cast sidecar (Chromecast / AirPlay / DLNA)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def cast_backend() -> str:
    return (os.environ.get("TORRENTSEARCH_CAST_BACKEND") or "native").strip().lower()


def find_webtorrent_cli() -> Optional[Path]:
    env = (os.environ.get("TORRENTSEARCH_WEBTORRENT") or "").strip()
    if env and Path(env).is_file():
        return Path(env)
    for name in ("webtorrent", "webtorrent.cmd", "webtorrent.ps1"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def start_webtorrent_cast(
    magnet_or_torrent: str,
    *,
    target: str = "chromecast",
    out_dir: Optional[Path] = None,
) -> dict:
    """
    Spawn `webtorrent-cli` with a cast flag.

    Requires TORRENTSEARCH_CAST_BACKEND=webtorrent and webtorrent-cli on PATH.
    target: chromecast | airplay | dlna | mpv | vlc
    """
    if cast_backend() != "webtorrent":
        raise RuntimeError(
            "Cast sidecar disabled — set TORRENTSEARCH_CAST_BACKEND=webtorrent"
        )
    exe = find_webtorrent_cli()
    if exe is None:
        raise FileNotFoundError(
            "webtorrent-cli not found — npm i -g webtorrent-cli "
            "or set TORRENTSEARCH_WEBTORRENT"
        )
    flag = {
        "chromecast": "--chromecast",
        "airplay": "--airplay",
        "dlna": "--dlna",
        "mpv": "--mpv",
        "vlc": "--vlc",
    }.get(target.lower())
    if not flag:
        raise ValueError(f"unsupported cast target '{target}'")

    args = [str(exe), magnet_or_torrent, flag, "--quiet"]
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        args.extend(["--out", str(out_dir)])

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return {
        "backend": "webtorrent",
        "target": target.lower(),
        "pid": proc.pid,
        "exe": str(exe),
    }
