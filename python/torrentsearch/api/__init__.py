"""API package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["app", "main"]


def __getattr__(name: str) -> Any:
    # Lazy exports avoid shadowing the `app` submodule with the FastAPI instance.
    if name == "app":
        from .app import app as _app

        return _app
    if name == "main":
        from .app import main as _main

        return _main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
