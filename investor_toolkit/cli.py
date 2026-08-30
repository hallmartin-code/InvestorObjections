"""Click CLI: deck in, one-pager PDF and investor objections out."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .mailer import MailError, send_run_copy
from .mailer import recipient as mail_recipient
from .models import DealData, ObjectionSet
from .objections import (
    ObjectionGenerationError,
    generate_objections,
    objections_to_list,
    objections_to_markdown,
)
from .one_pager import generate_one_pager
from .parser import extract_text
from .synthesizer import SynthesisError, extract_deal_data

console = Console()
err_console = Console(stderr=True)


def _fail(message: str) -> None:
    """Print an error and exit with code 1."""
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    sys.exit(1)


def _spinner() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    )


@click.command(name="investor-toolkit")
@click.argument("deck_path", type=click.Path(path_type=Path))
@click.option(
    "--context",
    "context",
    default="initial outreach",
    show_default=True,
    help='How the investor interaction occurred (e.g., "met at SaaStr 2025").',
)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./outputs"),
    show_default=True,
    help="Directory for output files.",
)
@click.option(
    "--objections-only", is_flag=True, help="Skip PDF generation, generate objections only."
)
@click.option(
    "--pdf-only", is_flag=True, help="Skip objection generation, produce PDF only."
)
@click.option(
    "--print-objections",
    is_flag=True,
    help="Print all ten objections and rebuttals to stdout after saving.",
)
@click.option(
    "--no-email",
    is_flag=True,
    help="Skip the archive email for this run (see RUN_COPY_TO in .env).",
)
def main(
    deck_path: Path,
    context: str,
    output_dir: Path,
    objections_only: bool,
    pdf_only: bool,
    print_objections: bool,
    no_email: bool,
) -> None:
    """Process an investor pitch deck into a one-pager PDF and ten objections with rebuttals."""
    # override=True: a project .env is an explicit, deliberate choice and should beat a
    # stale machine-wide variable. Railway sets real env vars and ships no .env file.
    load_dotenv(override=True)

    if objections_only and pdf_only:
        _fail("--objections-only and --pdf-only cannot be used together.")

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        _fail("ANTHROPIC_API_KEY not set. Add it to your .env file.")

    if not deck_path.exists():
        _fail(f"Deck file not found: {deck_path.resolve()}")
    if not deck_path.is_file():
        _fail(f"Deck path is not a file: {deck_path.resolve()}")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(f"Could not create output directory {output_dir}: {exc}")

    console.print(
        Panel.fit(
            f"[bold]{deck_path.name}[/bold]\ncontext: {context}",
            title="investor-toolkit",
            border_style="blue",
        )
    )

    # 1 — Parse the deck.
    with _spinner() as progress:
        progress.add_task("Parsing deck…", total=None)
        try:
            deck_text = extract_text(str(deck_path))
        except (ValueError, FileNotFoundError) as exc:
            _fail(str(exc))
        except Exception as exc:
            _fail(f"Failed to read deck: {exc}")
    if not deck_text.strip():
        _fail(f"No extractable text found in {deck_path.name}. Is the deck image-only?")
    console.print(f"[green]✓[/green] Parsed deck ({len(deck_text):,} characters)")

    # 2 — Extract structured deal data.
    with _spinner() as progress:
        progress.add_task("Extracting deal data…", total=None)
        try:
            deal: DealData = extract_deal_data(deck_text)
        except SynthesisError as exc:
            _fail(str(exc))
    console.print(f"[green]✓[/green] Extracted deal data for [bold]{deal.company_name}[/bold]")

    outputs: list[tuple[str, str]] = []
    items: ObjectionSet | None = None

    # 3 — One-pager PDF.
    if not objections_only:
        pdf_path = output_dir / f"{deal.slug()}_one_pager.pdf"
        with _spinner() as progress:
            progress.add_task("Generating one-pager…", total=None)
            try:
                generate_one_pager(deal, str(pdf_path))
            except Exception as exc:
                _fail(f"PDF generation failed: {exc}")
        outputs.append(("One-pager PDF", str(pdf_path)))
        console.print(f"[green]✓[/green] Wrote {pdf_path}")

    # 4 — Objections and rebuttals.
    if not pdf_only:
        md_path = output_dir / f"{deal.slug()}_objections.md"
        with _spinner() as progress:
            progress.add_task("Generating objections…", total=None)
            try:
                items = generate_objections(deal, context)
            except ObjectionGenerationError as exc:
                _fail(str(exc))
        try:
            md_path.write_text(objections_to_markdown(deal, items, context), encoding="utf-8")
        except OSError as exc:
            _fail(f"Could not write {md_path}: {exc}")
        outputs.append(("Objections & rebuttals", str(md_path)))
        console.print(f"[green]✓[/green] Wrote {md_path}")

    # 5 — Archive email. A failure here must not fail a completed run.
    archive_to = None if no_email else mail_recipient()
    if archive_to:
        with _spinner() as progress:
            progress.add_task("Emailing archive copy…", total=None)
            try:
                send_run_copy(
                    deal=deal,
                    deck_filename=deck_path.name,
                    context=context,
                    attachments=[Path(path) for _, path in outputs],
                    objections=objections_to_list(items) if items else [],
                )
            except MailError as exc:
                archive_to = None
                err_console.print(f"[yellow]![/yellow] Archive email not sent: {exc}")
        if archive_to:
            console.print(f"[green]✓[/green] Emailed archive copy to {archive_to}")

    _print_summary(deal, outputs, items)

    if print_objections and items is not None:
        _print_objections(items)


def _print_summary(
    deal: DealData, outputs: list[tuple[str, str]], items: ObjectionSet | None
) -> None:
    table = Table(title=f"{deal.company_name or 'Company'} — Output Summary", show_lines=False)
    table.add_column("Item", style="bold")
    table.add_column("Detail")
    table.add_column("Words", justify="right")

    for label, path in outputs:
        table.add_row(label, path, "")

    if items is not None:
        for entry in objections_to_list(items):
            table.add_row(f"Objection {entry['number']}", entry["title"], str(entry["words"]))

    console.print(table)


def _print_objections(items: ObjectionSet) -> None:
    for entry in objections_to_list(items):
        console.print()
        console.print(
            Panel(
                f"[bold]Objection[/bold]\n{entry['objection']}\n\n"
                f"[bold]Rebuttal[/bold]\n{entry['rebuttal']}",
                title=f"{entry['number']}. {entry['title']}",
                border_style="blue",
                padding=(1, 2),
            )
        )
