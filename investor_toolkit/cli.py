"""Click CLI: deck in, one-pager PDF and follow-up emails out."""

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

from .email_generator import EmailGenerationError, emails_to_markdown, generate_emails
from .models import EMAIL_TITLES, DealData, EmailSet
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
@click.option("--emails-only", is_flag=True, help="Skip PDF generation, generate emails only.")
@click.option("--pdf-only", is_flag=True, help="Skip email generation, produce PDF only.")
@click.option("--print-emails", is_flag=True, help="Print all five emails to stdout after saving.")
def main(
    deck_path: Path,
    context: str,
    output_dir: Path,
    emails_only: bool,
    pdf_only: bool,
    print_emails: bool,
) -> None:
    """Process an investor pitch deck and generate a one-pager PDF and follow-up emails."""
    # override=True: a project .env is an explicit, deliberate choice and should beat a
    # stale machine-wide variable. Railway sets real env vars and ships no .env file.
    load_dotenv(override=True)

    if emails_only and pdf_only:
        _fail("--emails-only and --pdf-only cannot be used together.")

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
    emails: EmailSet | None = None

    # 3 — One-pager PDF.
    if not emails_only:
        pdf_path = output_dir / f"{deal.slug()}_one_pager.pdf"
        with _spinner() as progress:
            progress.add_task("Generating one-pager…", total=None)
            try:
                generate_one_pager(deal, str(pdf_path))
            except Exception as exc:
                _fail(f"PDF generation failed: {exc}")
        outputs.append(("One-pager PDF", str(pdf_path)))
        console.print(f"[green]✓[/green] Wrote {pdf_path}")

    # 4 — Follow-up emails.
    if not pdf_only:
        md_path = output_dir / f"{deal.slug()}_follow_up_emails.md"
        with _spinner() as progress:
            progress.add_task("Generating emails…", total=None)
            try:
                emails = generate_emails(deal, context)
            except EmailGenerationError as exc:
                _fail(str(exc))
        try:
            md_path.write_text(emails_to_markdown(deal, emails, context), encoding="utf-8")
        except OSError as exc:
            _fail(f"Could not write {md_path}: {exc}")
        outputs.append(("Follow-up emails", str(md_path)))
        console.print(f"[green]✓[/green] Wrote {md_path}")

    _print_summary(deal, outputs, emails)

    if print_emails and emails is not None:
        _print_emails(emails)


def _print_summary(
    deal: DealData, outputs: list[tuple[str, str]], emails: EmailSet | None
) -> None:
    table = Table(title=f"{deal.company_name or 'Company'} — Output Summary", show_lines=False)
    table.add_column("Item", style="bold")
    table.add_column("Detail")
    table.add_column("Words", justify="right")

    for label, path in outputs:
        table.add_row(label, path, "")

    if emails is not None:
        for index, (key, title) in enumerate(EMAIL_TITLES.items(), start=1):
            body = getattr(emails, key)
            table.add_row(f"Email {index}", title, str(len(body.split())))

    console.print(table)


def _print_emails(emails: EmailSet) -> None:
    for index, (key, title) in enumerate(EMAIL_TITLES.items(), start=1):
        console.print()
        console.print(
            Panel(
                getattr(emails, key),
                title=f"Email {index} — {title}",
                border_style="blue",
                padding=(1, 2),
            )
        )


if __name__ == "__main__":  # pragma: no cover
    main()
