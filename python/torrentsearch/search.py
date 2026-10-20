"""Parallel search orchestration (Android TorrentQueryService)."""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Callable, Optional
from urllib.parse import quote

from .activity import ActivityLevel, activity
from .filters import apply_filters, boost_keyword_matches, is_alive, is_sfw
from .models import (
    Category,
    MagnetAvailable,
    MagnetRequiresFetch,
    SearchProviderError,
    SearchResults,
    SortCriteria,
    SortOrder,
    Torrent,
    TorrentDetails,
)
from .network import CloudflareChallengeException, NetworkClient
from .provider import MagnetUriProvider, SearchProvider, TorrentDetailsProvider
from .ranking import rank_torrents
from .utils import sort_torrents


class SearchService:
    def __init__(
        self,
        providers: list[SearchProvider],
        *,
        enabled_ids: Optional[set[str]] = None,
        nsfw: bool = False,
        hide_dead: bool = False,
        max_results: Optional[int] = None,
        sort_by: SortCriteria = SortCriteria.SEEDERS,
        sort_order: SortOrder = SortOrder.DESCENDING,
        boost_keywords: Optional[list[str]] = None,
    ):
        self.providers = providers
        self.enabled_ids = enabled_ids
        self.nsfw = nsfw
        self.hide_dead = hide_dead
        self.max_results = max_results
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.boost_keywords = boost_keywords or []
        self._magnet_cache: dict[str, str] = {}

    def _enabled_for(self, category: Category) -> list[SearchProvider]:
        out: list[SearchProvider] = []
        for p in self.providers:
            if self.enabled_ids is not None and p.id not in self.enabled_ids:
                continue
            if not self.nsfw and (
                p.safety.value == "unsafe"
                or Category.PORN in p.supported_categories
            ):
                # Safe mode: skip unsafe + NSFW-category providers (Android disableUnsafeProviders)
                if self.enabled_ids is None:
                    continue
            if not p.supports(category):
                continue
            out.append(p)
        return out

    def default_enabled_ids(self) -> set[str]:
        return {p.id for p in self.providers if p.enabled_by_default}

    async def search(
        self,
        query: str,
        category: Category = Category.ALL,
        *,
        provider_ids: Optional[set[str]] = None,
    ) -> SearchResults:
        providers = self._enabled_for(category)
        if provider_ids is not None:
            providers = [p for p in providers if p.id in provider_ids]
        await activity.begin_search()
        await activity.say(
            f"Looking up “{query}” across {len(providers)} sites…",
            area="search",
        )
        try:
            results = SearchResults()
            async for batch in self.search_stream(
                query, category, provider_ids=provider_ids
            ):
                results = batch
                if self.max_results is not None and len(results.torrents) >= self.max_results:
                    break
            torrents = self._post_process(results.torrents, query, category)
            if self.max_results is not None:
                torrents = torrents[: self.max_results]
            final = SearchResults(torrents=torrents, errors=results.errors)
            err_n = len(final.errors)
            if err_n:
                await activity.say(
                    f"Found {len(final.torrents)} torrents; {err_n} site(s) had trouble",
                    level=ActivityLevel.WARN,
                    area="search",
                )
            else:
                await activity.say(
                    f"Found {len(final.torrents)} torrents — all sites responded",
                    level=ActivityLevel.GOOD,
                    area="search",
                )
            return final
        except Exception as exc:
            await activity.say(
                f"Search ran into a problem: {exc}",
                level=ActivityLevel.BAD,
                area="search",
            )
            raise
        finally:
            await activity.end_search()

    async def search_stream(
        self,
        query: str,
        category: Category = Category.ALL,
        *,
        provider_ids: Optional[set[str]] = None,
    ) -> AsyncIterator[SearchResults]:
        providers = self._enabled_for(category)
        if provider_ids is not None:
            providers = [p for p in providers if p.id in provider_ids]
        if not providers:
            return

        encoded = quote(query, safe="")
        accumulated = SearchResults()
        tasks = [
            asyncio.create_task(self._run_provider(p, encoded, category), name=p.id)
            for p in providers
        ]
        # After the first hits, don't let a stuck site hold the rest.
        grace_s = 6.0
        deadline: float | None = None
        pending = set(tasks)
        try:
            while pending:
                timeout = None
                if deadline is not None:
                    timeout = max(0.0, deadline - time.monotonic())
                done, pending = await asyncio.wait(
                    pending,
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for fut in done:
                    torrents, error = fut.result()
                    if error:
                        accumulated = accumulated.add_error(error)
                    elif torrents:
                        accumulated = accumulated.add_success(torrents)
                        if deadline is None:
                            deadline = time.monotonic() + grace_s
                    yield SearchResults(
                        torrents=list(accumulated.torrents),
                        errors=list(accumulated.errors),
                    )
                    if (
                        self.max_results is not None
                        and len(accumulated.torrents) >= self.max_results
                    ):
                        pending.clear()
                        break
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_provider(
        self, provider: SearchProvider, encoded_query: str, category: Category
    ) -> tuple[list[Torrent], Optional[SearchProviderError]]:
        try:
            torrents = await asyncio.wait_for(
                provider.search(encoded_query, category),
                timeout=20.0,
            )
            return torrents, None
        except asyncio.TimeoutError:
            await activity.say(
                f"{provider.name} took too long — moved on",
                level=ActivityLevel.WARN,
                area="network",
            )
            return [], SearchProviderError(
                provider_id=provider.id,
                provider_name=provider.name,
                provider_url=provider.url,
                kind="timeout",
                cause=TimeoutError(f"{provider.name} timed out after 20s"),
            )
        except CloudflareChallengeException as exc:
            await activity.say(
                f"{provider.name} is behind a Cloudflare check — skipped for now",
                level=ActivityLevel.WARN,
                area="network",
            )
            return [], SearchProviderError(
                provider_id=provider.id,
                provider_name=provider.name,
                provider_url=provider.url,
                kind="cloudflare",
                cause=exc,
            )
        except Exception as exc:  # noqa: BLE001 — mirror Android runCatchingProvider
            await activity.say(
                f"{provider.name} didn’t respond this time",
                level=ActivityLevel.WARN,
                area="network",
            )
            return [], SearchProviderError(
                provider_id=provider.id,
                provider_name=provider.name,
                provider_url=provider.url,
                kind="crash",
                cause=exc,
            )

    def _post_process(
        self, torrents: list[Torrent], query: str, category: Category
    ) -> list[Torrent]:
        filters = []
        if not self.nsfw:
            filters.append(is_sfw())
        if self.hide_dead:
            filters.append(is_alive())
        filtered = apply_filters(torrents, *filters) if filters else torrents
        # Magnetio/Cauldron-style: hash dedupe + episode validate + quality score.
        # When the user asked for a non-default sort, still dedupe/filter first.
        ranked = rank_torrents(
            filtered,
            query,
            category=category,
            strict_episode=True,
        )
        if self.sort_by is not SortCriteria.SEEDERS:
            ranked = sort_torrents(ranked, self.sort_by, self.sort_order)
        if self.boost_keywords:
            return boost_keyword_matches(ranked, self.boost_keywords)
        return ranked

    async def resolve_magnet(self, torrent: Torrent) -> Optional[str]:
        if isinstance(torrent.magnet_uri, MagnetAvailable):
            return torrent.magnet_uri.value
        if torrent.id in self._magnet_cache:
            return self._magnet_cache[torrent.id]
        if not isinstance(torrent.magnet_uri, MagnetRequiresFetch):
            return None
        provider = next(
            (
                p
                for p in self.providers
                if p.name == torrent.provider_name and isinstance(p, MagnetUriProvider)
            ),
            None,
        )
        if provider is None:
            return None
        magnet = await provider.get_magnet_uri(torrent.magnet_uri.url)
        self._magnet_cache[torrent.id] = magnet
        return magnet

    async def resolve_magnets(
        self,
        torrents: list[Torrent],
        *,
        concurrency: int = 8,
        on_progress: Optional[Callable[[], None]] = None,
    ) -> tuple[int, int]:
        """Resolve RequiresFetch magnets in parallel. Returns (resolved, failed)."""
        pending = [
            t for t in torrents if isinstance(t.magnet_uri, MagnetRequiresFetch)
        ]
        if not pending:
            return 0, 0

        sem = asyncio.Semaphore(concurrency)
        resolved = 0
        failed = 0

        async def _one(t: Torrent) -> None:
            nonlocal resolved, failed
            async with sem:
                try:
                    magnet = await self.resolve_magnet(t)
                    if magnet:
                        t.magnet_uri = MagnetAvailable(magnet)
                        resolved += 1
                    else:
                        failed += 1
                except Exception:  # noqa: BLE001
                    failed += 1
                if on_progress:
                    on_progress()

        await asyncio.gather(*[_one(t) for t in pending])
        if resolved or failed:
            if failed and not resolved:
                await activity.say(
                    f"Couldn’t fetch magnet links ({failed} failed)",
                    level=ActivityLevel.WARN,
                    area="search",
                )
            elif failed:
                await activity.say(
                    f"Fetched {resolved} magnet links ({failed} still missing)",
                    level=ActivityLevel.WARN,
                    area="search",
                )
            else:
                await activity.say(
                    f"Fetched {resolved} magnet links",
                    level=ActivityLevel.GOOD,
                    area="search",
                )
        return resolved, failed

    async def get_details(
        self,
        details_page_url: str,
        *,
        provider_name: Optional[str] = None,
        provider_id: Optional[str] = None,
    ) -> Optional[TorrentDetails]:
        candidates: list[TorrentDetailsProvider] = [
            p for p in self.providers if isinstance(p, TorrentDetailsProvider)
        ]
        if provider_id:
            candidates = [p for p in candidates if p.id == provider_id]
        elif provider_name:
            name = provider_name.lower()
            candidates = [
                p
                for p in candidates
                if p.name.lower() == name or p.id.lower() == name
            ]
        else:
            matched = [
                p
                for p in candidates
                if p.url.rstrip("/").split("//", 1)[-1].split("/", 1)[0]
                in details_page_url
            ]
            if matched:
                candidates = matched

        last_error: Optional[BaseException] = None
        for provider in candidates:
            try:
                details = await provider.get_details(details_page_url)
                if details is not None:
                    return details
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        if last_error:
            raise last_error
        return None


def create_builtin_providers(network: NetworkClient) -> list[SearchProvider]:
    """Instantiate builtin scrapers + optional Torznab/Jackett indexers from env.

    Torznab entries (semicolon-separated):
      TORRENTSEARCH_TORZNAB=Name|http://127.0.0.1:9117/api/v2.0/indexers/all/results/torznab|APIKEY
    Multiple:
      TORRENTSEARCH_TORZNAB=Jackett|url|key;Prowlarr|url2|key2
    """
    import os
    import re

    from .models import Category, TorznabConfig
    from .providers import BUILTIN_PROVIDER_CLASSES
    from .providers.torznab import TorznabSearchProvider

    providers: list[SearchProvider] = [cls(network) for cls in BUILTIN_PROVIDER_CLASSES]

    raw = (os.environ.get("TORRENTSEARCH_TORZNAB") or "").strip()
    if raw:
        for i, entry in enumerate(raw.split(";")):
            entry = entry.strip()
            if not entry:
                continue
            parts = [p.strip() for p in entry.split("|")]
            if len(parts) < 3:
                continue
            name, url, api_key = parts[0], parts[1], parts[2]
            slug = re.sub(r"[^a-z0-9]+", "", name.lower()) or f"torznab{i}"
            cfg = TorznabConfig(
                id=f"torznab-{slug}",
                search_provider_name=name,
                url=url,
                api_key=api_key,
                supported_categories=set(Category),
            )
            providers.append(TorznabSearchProvider(cfg, network))

    return providers
