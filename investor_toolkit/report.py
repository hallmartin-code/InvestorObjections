"""Downloadable PDF report of the investor objections and rebuttals.

A multi-page companion to the one-pager: a navy title band, the deal context, then one
block per objection. Every page carries the standard TEN Capital footer —
``[Document Title]  [PAGE#]  Compiled on [DATE] by TEN Capital Network  [logo]`` — in
Open Sans 7pt when the font is installed, Helvetica otherwise.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table

from .models import DealData

PAGE_W, PAGE_H = letter
MARGIN = 0.75 * inch
FOOTER_SIZE = 7
LOGO_PATH = Path(__file__).parent / "static" / "TEN_Capital_logo_footer.png"
LOGO_W, LOGO_H = 0.67 * inch, 0.25 * inch  # CLAUDE.md: ~0.67" x 0.25"

NAVY = HexColor("#1B2A4A")
LABEL_GRAY = HexColor("#6B7A8D")
RULE_GRAY = HexColor("#D5DBE3")
PANEL_BG = HexColor("#F4F6F8")
FOOTER_TEXT = HexColor("#6B7A8D")

_FONT_CANDIDATES = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "OpenSans-Regular.ttf",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts/OpenSans-Regular.ttf",
    Path("/usr/share/fonts/truetype/open-sans/OpenSans-Regular.ttf"),
    Path("/usr/share/fonts/opentype/open-sans/OpenSans-Regular.ttf"),
    Path(__file__).parent / "static" / "OpenSans-Regular.ttf",
]


def _footer_font() -> str:
    """Open Sans if a TTF can be found, else Helvetica (always available in ReportLab)."""
    if "OpenSans" in pdfmetrics.getRegisteredFontNames():
        return "OpenSans"
    for candidate in _FONT_CANDIDATES:
        if candidate.is_file():
            try:
                pdfmetrics.registerFont(TTFont("OpenSans", str(candidate)))
                return "OpenSans"
            except Exception:  # a corrupt font file should not fail the report
                continue
    return "Helvetica"


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "meta": ParagraphStyle(
            "meta", fontName="Helvetica", fontSize=9.5, leading=14, textColor=NAVY
        ),
        "num": ParagraphStyle(
            "num", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=LABEL_GRAY
        ),
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=NAVY
        ),
        "label": ParagraphStyle(
            "label",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=10,
            textColor=LABEL_GRAY,
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=1,  # never strand a label at the foot of a page
        ),
        "body": ParagraphStyle(
            "body", fontName="Helvetica", fontSize=10, leading=14.5, textColor=HexColor("#111111")
        ),
    }


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    """Escape model text for ReportLab's mini-markup and keep paragraph breaks."""
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def generate_objections_report(
    deal: DealData,
    objections: list[dict],
    interaction_context: str,
    output_path: str,
    compiled_on: date | None = None,
) -> str:
    """Render ``objections`` (from ``objections_to_list``) as a PDF report; return its path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    company = deal.company_name or "Company"
    doc_title = f"{company} — Investor Objections & Rebuttals"
    day = compiled_on or date.today()
    compiled = f"{day:%B} {day.day}, {day.year}"
    footer_font = _footer_font()
    styles = _styles()

    band_h = 92

    def draw_page(canvas, doc) -> None:
        canvas.saveState()
        if doc.page == 1:
            canvas.setFillColor(NAVY)
            canvas.rect(0, PAGE_H - band_h, PAGE_W, band_h, stroke=0, fill=1)
            canvas.setFillColor(HexColor("#A8B8D0"))
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(MARGIN, PAGE_H - 34, "INVESTOR OBJECTIONS & REBUTTALS")
            canvas.setFillColor(HexColor("#FFFFFF"))
            canvas.setFont("Helvetica-Bold", 22)
            name = company
            while name and stringWidth(name, "Helvetica-Bold", 22) > PAGE_W - 2 * MARGIN:
                name = name[:-2] + "…"
            canvas.drawString(MARGIN, PAGE_H - 64, name)
        _draw_footer(canvas, doc.page, doc_title, compiled, footer_font)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(out),
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=doc_title,
        author="TEN Capital Network",
        subject=f"Investor objections for {company}",
    )

    story: list = [Spacer(1, band_h - MARGIN + 14)]
    meta_rows = [
        ("Interaction context", interaction_context or "initial outreach"),
        ("Stage", deal.stage or "Not stated"),
        ("Compiled", compiled),
    ]
    story.append(
        _para("  ·  ".join(f"{k}: {v}" for k, v in meta_rows), styles["meta"])
    )
    story.append(Spacer(1, 16))

    width = PAGE_W - 2 * MARGIN
    for item in objections:
        heading = Table(
            [[
                _para(f"{item['number']:02d}", styles["num"]),
                _para(item["title"], styles["title"]),
            ]],
            colWidths=[30, width - 30],
            style=[
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE_GRAY),
            ],
        )
        objection_panel = Table(
            [[_para(item["objection"], styles["body"])]],
            colWidths=[width],
            style=[
                ("BACKGROUND", (0, 0), (-1, -1), PANEL_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, LABEL_GRAY),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ],
        )
        # Keep the heading with the objection; the rebuttal may flow onto the next page.
        story.append(
            KeepTogether(
                [heading, Paragraph("OBJECTION", styles["label"]), objection_panel]
            )
        )
        story.append(Paragraph("REBUTTAL", styles["label"]))
        story.append(_para(item["rebuttal"], styles["body"]))
        story.append(Spacer(1, 20))

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return str(out)


def _draw_footer(canvas, page: int, title: str, compiled: str, font: str) -> None:
    """Single centered line: title, page number, compiled-on line, logo."""
    gap = "      "
    parts = [title, str(page), f"Compiled on {compiled} by TEN Capital Network"]
    text = gap.join(parts) + "  "
    logo = LOGO_PATH if LOGO_PATH.is_file() else None

    text_w = stringWidth(text, font, FOOTER_SIZE)
    total = text_w + (LOGO_W if logo else 0)
    x = (PAGE_W - total) / 2
    y = 0.4 * inch

    canvas.setFont(font, FOOTER_SIZE)
    canvas.setFillColor(FOOTER_TEXT)
    canvas.drawString(x, y, text)
    if logo:
        canvas.drawImage(
            str(logo), x + text_w, y - LOGO_H / 2 + 2, LOGO_W, LOGO_H, mask="auto"
        )
