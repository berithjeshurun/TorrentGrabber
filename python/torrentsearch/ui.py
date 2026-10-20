"""Rich terminal UI helpers."""

from __future__ import annotations

import sys
from typing import Iterable, Optional

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from . import __version__
from .export import magnet_of
from .models import SearchProviderError, Torrent, TorrentDetails


def _make_console(**kwargs) -> Console:
    # Avoid Windows cp1252 crashes on box-drawing / unicode glyphs.
    if sys.platform == "win32":
        kwargs.setdefault("legacy_windows", False)
    return Console(**kwargs)


console = _make_console(force_terminal=True)
err_console = _make_console(stderr=True)


def banner() -> None:
    console.print(
        Panel(
            Text.from_markup(
                f"[bold cyan]TorrentSearch[/] [dim]v{__version__}[/]\n"
                "[dim]Multi-provider torrent search CLI[/]"
            ),
            border_style="cyan",
            box=box.ROUNDED,
        )
    )


def search_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )


def print_results_table(
    torrents: list[Torrent],
    *,
    query: str,
    category: str,
    errors: Optional[list[SearchProviderError]] = None,
) -> None:
    if not torrents:
        console.print("[yellow]No results.[/]")
        return

    table = Table(
        title=f"[bold]Results[/] for [cyan]{query}[/] · {category}",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        header_style="bold",
        expand=True,
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Seed", justify="right", style="green", width=6)
    table.add_column("Peer", justify="right", style="red", width=6)
    table.add_column("Size", width=10)
    table.add_column("Provider", style="magenta", max_width=16)
    table.add_column("Cat", style="dim", max_width=8)
    table.add_column("Name", overflow="ellipsis", no_wrap=True, ratio=2)
    table.add_column("Mag", justify="center", width=4)

    for i, t in enumerate(torrents, 1):
        has_magnet = magnet_of(t) is not None
        magnet_mark = "[green]Y[/]" if has_magnet else "[dim]-[/]"
        name = t.name
        if t.is_nsfw:
            name = f"[red]{name}[/]"
        elif t.is_dead:
            name = f"[dim]{name}[/]"
        table.add_row(
            str(i),
            str(t.seeders) if t.seeders is not None else "-",
            str(t.peers) if t.peers is not None else "-",
            t.size or "-",
            t.provider_name,
            t.category.value if t.category else "-",
            name,
            magnet_mark,
        )

    console.print(table)
    summary = Text.assemble(
        (f"{len(torrents)}", "bold"),
        " result(s)",
    )
    if errors:
        summary.append_text(Text(f" · {len(errors)} provider error(s)", style="yellow"))
    console.print(summary)


def print_providers_table(rows: list[dict]) -> None:
    table = Table(
        title="Built-in search providers",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        expand=True,
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Default", justify="center", width=7)
    table.add_column("CF", justify="center", width=4)
    table.add_column("Safety", width=8)
    table.add_column("Categories", overflow="fold")

    for r in rows:
        safety = r["safety"]
        safety_style = "red" if safety == "unsafe" else "green"
        table.add_row(
            r["id"],
            r["name"],
            "[green]yes[/]" if r["default"] else "[dim]no[/]",
            "[yellow]yes[/]" if r["cloudflare"] else "[dim]no[/]",
            f"[{safety_style}]{safety}[/]",
            ", ".join(r["categories"]) or "-",
        )

    console.print(table)
    console.print(f"[dim]{len(rows)} providers[/]")


def _cause_text(exc: BaseException) -> str:
    text = str(exc).strip()
    nested = exc.__cause__ or exc.__context__
    if nested is not None:
        nested_text = str(nested).strip() or type(nested).__name__
        text = f"{text} | {nested_text}" if text else nested_text
    return text or type(exc).__name__


def print_errors(errors: Iterable[SearchProviderError], *, verbose: bool = True) -> None:
    errors = list(errors)
    if not errors:
        return
    if not verbose:
        err_console.print(
            f"[yellow]{len(errors)} provider error(s); use -v to show[/]"
        )
        return
    table = Table(title="Provider errors", box=box.SIMPLE, show_header=True)
    table.add_column("Provider", style="magenta")
    table.add_column("Kind", style="yellow")
    table.add_column("Message", overflow="fold")
    for e in errors:
        table.add_row(e.provider_name, e.kind, _cause_text(e.cause))
    err_console.print(table)


def print_details(details: TorrentDetails) -> None:
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style="bold dim", justify="right")
    meta.add_column()
    meta.add_row("Info hash", details.info_hash)
    meta.add_row("Size", details.size or "-")
    meta.add_row(
        "Seeders / Peers",
        f"{details.seeders if details.seeders is not None else '-'} / "
        f"{details.peers if details.peers is not None else '-'}",
    )
    meta.add_row(
        "Uploaded",
        details.upload_date.isoformat() if details.upload_date else "-",
    )
    meta.add_row("Category", details.category.value if details.category else "-")
    meta.add_row("Uploader", details.uploader or "-")
    if details.poster_url:
        meta.add_row("Poster", details.poster_url)
    if details.file_download_link:
        meta.add_row("Torrent file", details.file_download_link)

    body_parts: list = [
        Text(details.name, style="bold cyan"),
        Text(""),
        meta,
        Text(""),
        Text(details.magnet_uri, style="green"),
    ]
    if details.description:
        body_parts.extend([Text(""), Markdown(details.description)])
    if details.screenshot_urls:
        shots = "\n".join(f"- {u}" for u in details.screenshot_urls)
        body_parts.extend([Text(""), Text("Screenshots", style="bold"), Text(shots)])

    console.print(
        Panel(
            Group(*body_parts),
            title="Torrent details",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )


def print_dump_ok(path: str, fmt: str, count: int) -> None:
    console.print(
        f"[green]Dumped[/] {count} result(s) as [bold]{fmt}[/] -> [cyan]{path}[/]"
    )
