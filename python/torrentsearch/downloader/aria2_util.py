"""Locate bundled aria2c under python/etc."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

_ETC = Path(__file__).resolve().parents[2] / "etc"


def aria2c_path() -> Optional[Path]:
    suffix = ".exe" if sys.platform == "win32" else ""
    local = _ETC / f"aria2c{suffix}"
    if local.is_file():
        return local
    which = shutil.which("aria2c")
    return Path(which) if which else None


def aria2_available() -> bool:
    return aria2c_path() is not None
