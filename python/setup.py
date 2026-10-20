#!/usr/bin/env python3
"""setuptools installer for the torrentsearch library."""

from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).resolve().parent


def _readme() -> str:
    path = ROOT / "README.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "TorrentSearch — multi-provider torrent search CLI + FastAPI service"


setup(
    name="torrentsearch",
    version="0.5.2",
    description="TorrentSearch — multi-provider torrent search CLI + FastAPI service",
    long_description=_readme(),
    long_description_content_type="text/markdown",
    license="GPL-3.0-or-later",
    python_requires=">=3.10",
    packages=find_packages(where=".", include=["torrentsearch", "torrentsearch.*"]),
    include_package_data=True,
    package_data={
        "torrentsearch": [
            "static/**/*",
            "downloader/node_stream/server.js",
            "downloader/node_stream/package.json",
        ],
    },
    install_requires=[
        "httpx>=0.27.0",
        "beautifulsoup4>=4.12.0",
        "lxml>=5.0.0",
        "rich>=13.7.0",
        "fastapi>=0.115.0",
        "uvicorn[standard]>=0.30.0",
        "sqlmodel>=0.0.22",
        "aiosqlite>=0.20.0",
        "greenlet>=3.0.0",
        "pydantic>=2.0.0",
    ],
    entry_points={
        "console_scripts": [
            "torrentsearch=torrentsearch.cli:main",
            "torrentsearch-api=torrentsearch.api.app:main",
        ],
    },
    zip_safe=False,
)
