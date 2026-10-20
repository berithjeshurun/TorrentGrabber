"""TorrentSearch rich CLI — multi-provider torrent search."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from . import __version__
from .export import (
    EXPORT_FORMATS,
    details_to_dict,
    dump_results,
    format_json,
    infer_format_from_path,
    render_export,
)
from .models import (
    Category,
    MagnetRequiresFetch,
    SortCriteria,
    SortOrder,
)
from .filters import parse_filter_keywords
from .network import NetworkClient
from .search import SearchService, create_builtin_providers
from . import ui


def _category(value: str) -> Category:
    key = value.strip().lower()
    for cat in Category:
        if cat.value.lower() == key or cat.name.lower() == key:
            return cat
    raise argparse.ArgumentTypeError(
        f"unknown category '{value}'. Choose from: "
        + ", ".join(c.value for c in Category)
    )


def _sort_criteria(value: str) -> SortCriteria:
    key = value.strip().lower()
    for c in SortCriteria:
        if c.value == key or c.name.lower() == key:
            return c
    raise argparse.ArgumentTypeError(
        f"unknown sort '{value}'. Choose from: "
        + ", ".join(c.value for c in SortCriteria)
    )


def _add_common_search_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-c",
        "--category",
        type=_category,
        default=Category.ALL,
        help="Category filter (default: All)",
    )
    p.add_argument(
        "-p",
        "--providers",
        help="Comma-separated provider IDs (default: enabled-by-default set)",
    )
    p.add_argument(
        "--all-providers",
        action="store_true",
        help="Use every built-in provider (still respects --nsfw / safe mode)",
    )
    p.add_argument(
        "--nsfw",
        action="store_true",
        help="Enable NSFW mode (include unsafe / porn providers & results)",
    )
    p.add_argument(
        "--hide-dead",
        action="store_true",
        help="Hide torrents with 0 seeders and 0 peers",
    )
    p.add_argument(
        "-n",
        "--max-results",
        type=int,
        default=None,
        help="Maximum number of results",
    )
    p.add_argument(
        "--sort",
        type=_sort_criteria,
        default=SortCriteria.SEEDERS,
        help="Sort: name, seeders, peers, size, date (default: seeders)",
    )
    p.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="desc",
        help="Sort order (default: desc)",
    )
    p.add_argument(
        "--filter",
        dest="filter_keywords",
        metavar="KEYWORDS",
        help=(
            "Comma-separated keywords; matching titles are ranked to the top "
            "(more matches rank higher). Example: --filter 1080p,x265,BluRay"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="torrentsearch",
        description="Rich multi-provider torrent search CLI (TorrentSearch).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search torrents across providers")
    search.add_argument("query", help="Search query")
    _add_common_search_flags(search)
    search.add_argument(
        "-f",
        "--format",
        choices=[*EXPORT_FORMATS],
        default="table",
        help="Stdout format: table (rich), json, csv, tsv, xml, magnets",
    )
    search.add_argument(
        "-o",
        "--dump",
        metavar="PATH",
        help="Also write results to PATH (format from extension or --dump-format)",
    )
    search.add_argument(
        "--dump-format",
        choices=["json", "csv", "tsv", "xml", "magnets"],
        default=None,
        help="Override dump file format (default: infer from --dump extension)",
    )
    search.add_argument(
        "--no-resolve-magnets",
        action="store_true",
        help="Do not fetch magnets that require a details page (faster)",
    )
    search.add_argument(
        "--resolve-concurrency",
        type=int,
        default=8,
        help="Parallel magnet fetches (default: 8)",
    )
    search.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show per-provider errors",
    )
    search.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress banner / progress",
    )

    providers = sub.add_parser("providers", help="List built-in search providers")
    providers.add_argument(
        "-f",
        "--format",
        choices=["table", "json"],
        default="table",
    )
    providers.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress banner",
    )

    details = sub.add_parser("details", help="Fetch torrent details from a details page URL")
    details.add_argument("url", help="Details page URL")
    details.add_argument(
        "--provider",
        help="Provider id or name (optional; auto-detected from URL when possible)",
    )
    details.add_argument(
        "-f",
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    details.add_argument(
        "-o",
        "--dump",
        metavar="PATH",
        help="Write details JSON to PATH",
    )
    details.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress banner / progress",
    )

    return p


def _resolve_enabled(
    all_providers,
    args: argparse.Namespace,
) -> set[str] | int:
    by_id = {p.id: p for p in all_providers}

    if args.providers:
        ids = {x.strip() for x in args.providers.split(",") if x.strip()}
        unknown = ids - set(by_id)
        if unknown:
            ui.err_console.print(
                f"[red]Unknown provider IDs:[/] {', '.join(sorted(unknown))}"
            )
            return 2
        return ids

    if getattr(args, "all_providers", False):
        enabled = set(by_id)
    else:
        enabled = {p.id for p in all_providers if p.enabled_by_default}

    if not args.nsfw:
        enabled = {
            pid
            for pid in enabled
            if by_id[pid].safety.value != "unsafe"
            and Category.PORN not in by_id[pid].supported_categories
        }
    return enabled


async def cmd_providers(args: argparse.Namespace) -> int:
    if not args.quiet:
        ui.banner()
    async with NetworkClient() as network:
        providers = create_builtin_providers(network)
        rows = []
        for p in sorted(providers, key=lambda x: x.name.lower()):
            rows.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "url": p.url,
                    "default": p.enabled_by_default,
                    "cloudflare": p.is_cloudflare_protected,
                    "safety": p.safety.value,
                    "categories": sorted(c.value for c in p.supported_categories),
                }
            )
        if args.format == "json":
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            ui.print_providers_table(rows)
    return 0


async def cmd_search(args: argparse.Namespace) -> int:
    if not args.quiet and args.format == "table":
        ui.banner()

    order = SortOrder.ASCENDING if args.order == "asc" else SortOrder.DESCENDING

    async with NetworkClient() as network:
        all_providers = create_builtin_providers(network)
        enabled = _resolve_enabled(all_providers, args)
        if isinstance(enabled, int):
            return enabled

        service = SearchService(
            all_providers,
            enabled_ids=enabled,
            nsfw=args.nsfw,
            hide_dead=args.hide_dead,
            max_results=args.max_results,
            sort_by=args.sort,
            sort_order=order,
            boost_keywords=parse_filter_keywords(
                getattr(args, "filter_keywords", None)
            ),
        )

        provider_count = len([p for p in all_providers if p.id in enabled and p.supports(args.category)])

        if args.quiet or args.format != "table":
            results = await service.search(args.query, args.category)
        else:
            with ui.search_progress() as progress:
                task = progress.add_task(
                    f"Searching [cyan]{args.query}[/] ({provider_count} providers)…",
                    total=None,
                )
                results = await service.search(args.query, args.category)
                progress.update(
                    task,
                    description=f"Found [bold]{len(results.torrents)}[/] result(s)",
                    completed=1,
                    total=1,
                )

        # Magnet resolve is ON by default.
        if not args.no_resolve_magnets:
            pending = sum(
                1 for t in results.torrents if isinstance(t.magnet_uri, MagnetRequiresFetch)
            )
            if pending:
                if args.quiet or args.format != "table":
                    await service.resolve_magnets(
                        results.torrents,
                        concurrency=args.resolve_concurrency,
                    )
                else:
                    with ui.search_progress() as progress:
                        task = progress.add_task(
                            "Resolving magnets…",
                            total=pending,
                        )

                        def _tick() -> None:
                            progress.advance(task)

                        resolved, failed = await service.resolve_magnets(
                            results.torrents,
                            concurrency=args.resolve_concurrency,
                            on_progress=_tick,
                        )
                        progress.update(
                            task,
                            description=(
                                f"Magnets: [green]{resolved}[/] resolved"
                                + (f", [red]{failed}[/] failed" if failed else "")
                            ),
                        )

        dump_fmt: Optional[str] = None
        if args.dump:
            dump_fmt = args.dump_format or infer_format_from_path(args.dump) or "json"
            path = dump_results(
                results,
                args.dump,
                dump_fmt,
                query=args.query,
                category=args.category.value,
            )
            if args.format == "table" and not args.quiet:
                ui.print_dump_ok(str(path), dump_fmt, len(results.torrents))
            elif args.quiet and args.format == "table":
                print(str(path))

        # Stdout rendering
        if args.format == "table":
            ui.print_results_table(
                results.torrents,
                query=args.query,
                category=args.category.value,
                errors=results.errors,
            )
            ui.print_errors(results.errors, verbose=args.verbose)
        else:
            # Machine formats go to stdout (pipe-friendly). Errors on stderr when verbose.
            sys.stdout.write(
                render_export(
                    results,
                    args.format,
                    query=args.query,
                    category=args.category.value,
                )
            )
            if args.verbose:
                ui.print_errors(results.errors, verbose=True)
            elif results.errors and not args.quiet:
                ui.print_errors(results.errors, verbose=False)

    return 0


async def cmd_details(args: argparse.Namespace) -> int:
    if not args.quiet and args.format == "table":
        ui.banner()

    async with NetworkClient() as network:
        providers = create_builtin_providers(network)
        service = SearchService(providers, enabled_ids={p.id for p in providers}, nsfw=True)

        if args.quiet or args.format != "table":
            details = await service.get_details(
                args.url,
                provider_name=args.provider,
            )
        else:
            with ui.search_progress() as progress:
                task = progress.add_task("Fetching details…", total=None)
                details = await service.get_details(
                    args.url,
                    provider_name=args.provider,
                )
                progress.update(task, completed=1, total=1)

        if details is None:
            ui.err_console.print("[red]Could not fetch torrent details for that URL.[/]")
            return 1

        if args.dump:
            path = args.dump
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(format_json(details_to_dict(details)))
            if args.format == "table" and not args.quiet:
                ui.print_dump_ok(path, "json", 1)

        if args.format == "json":
            sys.stdout.write(format_json(details_to_dict(details)))
        else:
            ui.print_details(details)

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    # Prefer UTF-8 on Windows consoles so Rich tables don't crash.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "quiet"):
        args.quiet = False

    try:
        if args.command == "providers":
            return asyncio.run(cmd_providers(args))
        if args.command == "search":
            return asyncio.run(cmd_search(args))
        if args.command == "details":
            return asyncio.run(cmd_details(args))
    except KeyboardInterrupt:
        ui.err_console.print("\n[yellow]Interrupted[/]")
        return 130

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
