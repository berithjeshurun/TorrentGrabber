"""Shared helpers for selecting which providers to run."""

from __future__ import annotations

from typing import Optional

from .models import Category
from .provider import SearchProvider


def resolve_enabled_provider_ids(
    providers: list[SearchProvider],
    *,
    provider_ids: Optional[set[str]] = None,
    all_providers: bool = False,
    nsfw: bool = False,
) -> set[str]:
    """
    Resolve the set of provider IDs to enable for a search.

    - Explicit provider_ids wins (caller should validate unknowns).
    - Else all_providers / defaults, then strip unsafe+porn providers unless nsfw.
    """
    by_id = {p.id: p for p in providers}

    if provider_ids is not None:
        return set(provider_ids)

    if all_providers:
        enabled = set(by_id)
    else:
        enabled = {p.id for p in providers if p.enabled_by_default}

    if not nsfw:
        enabled = {
            pid
            for pid in enabled
            if by_id[pid].safety.value != "unsafe"
            and Category.PORN not in by_id[pid].supported_categories
        }
    return enabled
