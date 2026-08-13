"""Single-page investor one-pager, drawn directly on a ReportLab canvas.

Layout, typography, palette and section order all come from the document template
(``templates/one_pager_template.json``); this module only resolves that template into
coordinates and draws it.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

from .models import DealData
from .template import TemplateError, load_template

PAGE_W, PAGE_H = letter


def generate_one_pager(deal: DealData, output_path: str) -> str:
    """Render ``deal`` as a one-page PDF at ``output_path`` and return that path."""
    return _render(deal, output_path, placeholders=False)


def render_template_skeleton(output_path: str) -> str:
    """Render the empty structural template — labels and ``{{field}}`` placeholders only.

    Useful for reviewing the document structure without a deck.
    """
    return _render(DealData(), output_path, placeholders=True)


def _render(deal: DealData, output_path: str, *, placeholders: bool) -> str:
    tpl = load_template()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    name = deal.company_name or ("{{company_name}}" if placeholders else "Company")
    c = pdfcanvas.Canvas(str(out), pagesize=letter)
    c.setTitle(f"{name} — Investor One-Pager")
    c.setAuthor(deal.company_name or "")

    _draw_header(c, tpl, deal, placeholders)
    _draw_body(c, tpl, deal, placeholders)
    _draw_footer(c, tpl, deal, placeholders)

    c.showPage()
    c.save()
    return str(out)


# --- template resolution ------------------------------------------------------------


def _color(tpl: dict, name: str):
    try:
        return HexColor(tpl["palette"][name])
    except KeyError as exc:
        raise TemplateError(f"Template palette has no color '{name}'.") from exc


def _font(tpl: dict, name: str) -> str:
    typography = tpl["typography"]
    return str(typography.get(name, typography.get("body_font", "Helvetica")))


def _value(deal: DealData, field: str, placeholders: bool) -> str:
    text = (getattr(deal, field, "") or "").strip()
    if text:
        return text
    return "{{" + field + "}}" if placeholders else ""


# --- drawing ------------------------------------------------------------------------


def _draw_header(c: pdfcanvas.Canvas, tpl: dict, deal: DealData, placeholders: bool) -> None:
    header = tpl["header"]
    height = float(header["height_pt"])
    margin = float(tpl["page"]["margin_pt"])

    c.setFillColor(_color(tpl, header["background"]))
    c.rect(0, PAGE_H - height, PAGE_W, height, stroke=0, fill=1)

    width = PAGE_W - 2 * margin
    for element in header["elements"]:
        text = _value(deal, element["field"], placeholders)
        if not text and element.get("required"):
            text = element.get("placeholder", "")
        if not text:
            continue
        font = _font(tpl, element["font"])
        size = float(element["size_pt"])
        if element.get("single_line", True):
            text = _fit_line(text, font, size, width, tpl)
        c.setFillColor(_color(tpl, element["color"]))
        c.setFont(font, size)
        c.drawString(margin, PAGE_H - float(element["baseline_from_top_pt"]), text)


def _draw_body(c: pdfcanvas.Canvas, tpl: dict, deal: DealData, placeholders: bool) -> None:
    page, body = tpl["page"], tpl["body"]
    margin = float(page["margin_pt"])
    gutter = float(page["gutter_pt"])

    columns = body["columns"]
    usable = (PAGE_W - 2 * margin) - gutter * (len(columns) - 1)

    top_y = PAGE_H - float(tpl["header"]["height_pt"]) - float(body["top_padding_pt"])
    bottom_y = float(tpl["footer"]["height_pt"]) + float(body["bottom_padding_pt"])
    available = top_y - bottom_y

    x = margin
    for column in columns:
        width = usable * float(column["width_ratio"])
        blocks = _layout_column(tpl, deal, column["sections"], width, available, placeholders)
        _draw_column(c, tpl, x, top_y, width, blocks)
        x += width + gutter


def _layout_column(
    tpl: dict,
    deal: DealData,
    sections: list[dict],
    width: float,
    available: float,
    placeholders: bool,
) -> list[tuple[str, list[str]]]:
    """Wrap each section to ``width`` and trim the column until it fits ``available``."""
    typo = tpl["typography"]
    font = _font(tpl, "body_font")
    size = float(typo["content_size_pt"])

    blocks: list[tuple[str, list[str]]] = []
    for section in sections:
        text = _value(deal, section["field"], placeholders)
        if not text:
            continue  # rules.empty_fields == "omit_section"
        lines = _wrap(text, font, size, width)
        if lines:
            blocks.append((str(section["label"]), lines))

    # Trim the longest section one line at a time until the column fits; if even a
    # single line per section is too much, drop whole sections from the bottom up.
    while blocks and _column_height(tpl, blocks) > available:
        longest = max(range(len(blocks)), key=lambda i: len(blocks[i][1]))
        if len(blocks[longest][1]) > 1:
            label, lines = blocks[longest]
            kept = lines[:-1]
            kept[-1] = _ellipsize(kept[-1], font, size, width, tpl)
            blocks[longest] = (label, kept)
        else:
            blocks.pop()

    return blocks


def _column_height(tpl: dict, blocks: list[tuple[str, list[str]]]) -> float:
    typo = tpl["typography"]
    label_h = float(typo["label_size_pt"]) + float(typo["label_gap_pt"])
    leading = float(typo["content_leading_pt"])
    gap = float(typo["section_gap_pt"])
    total = sum(label_h + len(lines) * leading + gap for _, lines in blocks)
    return total - gap if blocks else 0.0


def _draw_column(
    c: pdfcanvas.Canvas,
    tpl: dict,
    x: float,
    top_y: float,
    width: float,
    blocks: list[tuple[str, list[str]]],
) -> None:
    typo = tpl["typography"]
    label_font = _font(tpl, "bold_font")
    label_size = float(typo["label_size_pt"])
    body_font = _font(tpl, "body_font")
    content_size = float(typo["content_size_pt"])
    leading = float(typo["content_leading_pt"])
    label_color = _color(tpl, "label_gray")
    text_color = _color(tpl, "black")

    y = top_y
    for label, lines in blocks:
        c.setFillColor(label_color)
        c.setFont(label_font, label_size)
        heading = _fit_line(label.upper(), label_font, label_size, width, tpl)
        c.drawString(x, y - label_size, heading)
        y -= label_size + float(typo["label_gap_pt"])

        c.setFillColor(text_color)
        c.setFont(body_font, content_size)
        for line in lines:
            y -= leading
            c.drawString(x, y, line)
        y -= float(typo["section_gap_pt"])


def _draw_footer(c: pdfcanvas.Canvas, tpl: dict, deal: DealData, placeholders: bool) -> None:
    footer = tpl["footer"]
    margin = float(tpl["page"]["margin_pt"])
    height = float(footer["height_pt"])
    font = _font(tpl, footer["font"])
    size = float(footer["size_pt"])

    c.setFillColor(_color(tpl, footer["background"]))
    c.rect(0, 0, PAGE_W, height, stroke=0, fill=1)

    c.setFillColor(_color(tpl, footer["color"]))
    c.setFont(font, size)
    baseline = height / 2 - size / 2 + 1
    c.drawString(margin, baseline, str(footer["left_text"]))

    contact = _value(deal, str(footer["right_field"]), placeholders).replace("\n", " ").strip()
    if contact:
        max_width = (PAGE_W - 2 * margin) * float(footer.get("right_max_width_ratio", 0.5))
        c.drawRightString(PAGE_W - margin, baseline, _fit_line(contact, font, size, max_width, tpl))


# --- text helpers -------------------------------------------------------------------


def _ellipsis(tpl: dict) -> str:
    return str(tpl.get("rules", {}).get("ellipsis", "…"))


def _wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap that also hard-breaks words wider than the column."""
    lines: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        words = para.split()
        if not words:
            continue
        current = ""
        for word in words:
            for piece in _split_long_word(word, font, size, max_width):
                trial = f"{current} {piece}".strip()
                if not current or stringWidth(trial, font, size) <= max_width:
                    current = trial
                else:
                    lines.append(current)
                    current = piece
        if current:
            lines.append(current)
    return lines


def _split_long_word(word: str, font: str, size: float, max_width: float) -> list[str]:
    if stringWidth(word, font, size) <= max_width:
        return [word]
    pieces: list[str] = []
    current = ""
    for ch in word:
        if current and stringWidth(current + ch, font, size) > max_width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


def _fit_line(text: str, font: str, size: float, max_width: float, tpl: dict) -> str:
    """Single-line fit: truncate with an ellipsis if the text is too wide."""
    text = " ".join(text.split())
    if stringWidth(text, font, size) <= max_width:
        return text
    return _ellipsize(text, font, size, max_width, tpl)


def _ellipsize(text: str, font: str, size: float, max_width: float, tpl: dict) -> str:
    mark = _ellipsis(tpl)
    text = text.rstrip()
    if text.endswith(mark):
        return text
    while text and stringWidth(text + mark, font, size) > max_width:
        text = text[:-1].rstrip()
    return (text + mark) if text else mark


if __name__ == "__main__":  # pragma: no cover - manual structure preview
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "outputs/one_pager_template.pdf"
    print(render_template_skeleton(target))
