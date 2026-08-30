"""Archive copy of every completed run, emailed through Resend.

The message carries the analysis itself — the extracted deal summary and the ten investor
objections with rebuttals, rendered inline — with the one-pager PDF and the Markdown file
attached, so the result
is readable straight from the inbox without opening anything.

``RESEND_API_KEY`` is the only variable that must be configured per environment — it is the
one real secret. The sender and recipient fall back to the TEN Capital defaults below, so a new
deployment needs one variable rather than three. Setting a sender or recipient *without* the API
key is still a misconfiguration and is surfaced rather than silently ignored — a disclosure that
promises an emailed copy must not quietly stop being true.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .models import DealData

# Defaults so only the secret has to be set per environment. Override with MAIL_FROM /
# RUN_COPY_TO. The sender's domain must be verified in the Resend dashboard.
DEFAULT_SENDER = "TEN Capital Deck Analyzer <deck-analyzer@tencapital.group>"
DEFAULT_RECIPIENT = "Info@tencapital.group"

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 20.0

# Resend caps a request at 40MB; our artifacts are a few hundred KB at most.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

# Deal fields rendered in the summary block, in order.
SUMMARY_FIELDS = [
    ("Stage", "stage"),
    ("Ask", "ask"),
    ("Why now", "why_now"),
    ("Traction", "traction"),
    ("Market size", "market_size"),
    ("Business model", "business_model"),
    ("Team", "team"),
    ("Competitive moat", "competitive_moat"),
    ("Key risks retired", "key_risks_retired"),
    ("Contact", "contact"),
]

NAVY = "#0B1526"
INK = "#1a1a1a"
MUTED = "#5C6E86"
RULE = "#DFE4EA"


class MailError(RuntimeError):
    """Raised when an archive copy cannot be sent."""


@dataclass(frozen=True)
class MailConfig:
    api_key: str
    sender: str
    recipient: str


def load_config() -> MailConfig | None:
    """Return the mail configuration, or None when the feature is switched off.

    Raises:
        MailError: some but not all of the required variables are set.
    """
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    sender = os.environ.get("MAIL_FROM", "").strip()
    recipient = os.environ.get("RUN_COPY_TO", "").strip()

    if not api_key:
        # Sender/recipient without the key means someone configured half of it.
        configured = [n for n, v in (("MAIL_FROM", sender), ("RUN_COPY_TO", recipient)) if v]
        if configured:
            raise MailError(
                "Run-copy email is partially configured: "
                + ", ".join(configured)
                + " is set but RESEND_API_KEY is missing."
            )
        return None

    return MailConfig(
        api_key=api_key,
        sender=sender or DEFAULT_SENDER,
        recipient=recipient or DEFAULT_RECIPIENT,
    )


def recipient() -> str | None:
    """The archive recipient, or None when mail is off or misconfigured."""
    try:
        config = load_config()
    except MailError:
        return None
    return config.recipient if config else None


# --- body rendering -------------------------------------------------------------------


def _render_text(
    deal: DealData, objections: list[dict], deck_filename: str, context: str
) -> str:
    lines = [
        f"{deal.company_name or 'Unknown company'}",
        f"{deal.tagline}" if deal.tagline else "",
        "",
        f"Source deck: {deck_filename}",
        f"Interaction context: {context}",
        f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        "",
        "DEAL SUMMARY",
        "",
    ]
    for label, field in SUMMARY_FIELDS:
        value = (getattr(deal, field, "") or "").strip()
        if value:
            lines.append(f"{label}: {value}")

    if objections:
        lines += ["", "INVESTOR OBJECTIONS & REBUTTALS", ""]
        for item in objections:
            lines += [
                f"--- {item['number']}. {item['title']} ---",
                "",
                "OBJECTION: " + item["objection"].strip(),
                "",
                "REBUTTAL: " + item["rebuttal"].strip(),
                "",
            ]

    lines += ["", "Confidential — For Accredited Investors Only"]
    return "\n".join(line for line in lines if line is not None)


def _render_html(
    deal: DealData, objections: list[dict], deck_filename: str, context: str
) -> str:
    def para(text: str) -> str:
        # Email clients are unreliable with white-space:pre-wrap; use real paragraphs.
        blocks = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
        return "".join(
            f'<p style="margin:0 0 12px;line-height:1.6;color:{INK}">'
            f"{escape(b).replace(chr(10), '<br>')}</p>"
            for b in blocks
        )

    rows = ""
    for label, field in SUMMARY_FIELDS:
        value = (getattr(deal, field, "") or "").strip()
        if not value:
            continue
        rows += (
            '<tr>'
            f'<td style="padding:7px 14px 7px 0;vertical-align:top;white-space:nowrap;'
            f'font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{MUTED}">'
            f"{escape(label)}</td>"
            f'<td style="padding:7px 0;vertical-align:top;font-size:14px;color:{INK};'
            f'line-height:1.55">{escape(value)}</td>'
            "</tr>"
        )

    objection_html = ""
    for item in objections:
        objection_html += (
            f'<div style="padding:18px 0;border-top:1px solid {RULE}">'
            f'<div style="font-size:13px;font-weight:700;color:{NAVY};margin-bottom:10px">'
            f"{item['number']:02d} &nbsp;{escape(item['title'])}</div>"
            f'<div style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
            f'color:{MUTED};margin-bottom:6px">Objection</div>'
            f"{para(item['objection'])}"
            f'<div style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
            f'color:{MUTED};margin:14px 0 6px">Rebuttal</div>'
            f"{para(item['rebuttal'])}"
            "</div>"
        )

    heading = escape(deal.company_name or "Unknown company")
    tagline = (
        f'<div style="font-size:14px;color:#A8B8D0;margin-top:4px">{escape(deal.tagline)}</div>'
        if deal.tagline
        else ""
    )

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#F4F6F8">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;padding:24px 12px">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0"
       style="max-width:640px;background:#ffffff;border-radius:10px;overflow:hidden;
              font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">

  <tr><td style="background:{NAVY};padding:26px 30px">
    <div style="font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#4FC4D6">
      Deck Analysis</div>
    <div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:8px">{heading}</div>
    {tagline}
  </td></tr>

  <tr><td style="padding:20px 30px;border-bottom:1px solid {RULE};font-size:12px;color:{MUTED};
                 line-height:1.7">
    <b style="color:{INK}">Source deck</b> {escape(deck_filename)}<br>
    <b style="color:{INK}">Interaction context</b> {escape(context)}<br>
    <b style="color:{INK}">Generated</b> {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC
  </td></tr>

  <tr><td style="padding:24px 30px 8px">
    <div style="font-size:13px;font-weight:700;color:{NAVY};margin-bottom:12px">Deal summary</div>
    <table cellpadding="0" cellspacing="0" width="100%">{rows}</table>
  </td></tr>

  {'<tr><td style="padding:16px 30px 24px">'
   f'<div style="font-size:13px;font-weight:700;color:{NAVY};margin-bottom:4px">'
   'Investor objections &amp; rebuttals</div>' + objection_html + '</td></tr>'
   if objections else ''}

  <tr><td style="background:#F4F6F8;padding:16px 30px;font-size:11px;color:{MUTED};
                 text-align:center">
    Confidential — For Accredited Investors Only · Generated by TEN Capital Network
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


# --- sending --------------------------------------------------------------------------


def send_run_copy(
    *,
    deal: DealData,
    deck_filename: str,
    context: str,
    attachments: list[Path],
    objections: list[dict] | None = None,
    config: MailConfig | None = None,
) -> str:
    """Email the run's analysis and artifacts to the configured archive address.

    Args:
        deal: the extracted deal data, rendered as the summary block.
        deck_filename: the uploaded deck's original name.
        context: the interaction context the run used.
        attachments: files to attach; missing paths are skipped.
        objections: the objections as ``{number, title, objection, rebuttal}`` dicts.

    Returns the Resend message id.

    Raises:
        MailError: mail is off, misconfigured, the payload is too large, or Resend refused.
    """
    config = config or load_config()
    if config is None:
        raise MailError("Run-copy email is not configured.")

    objections = objections or []
    files = [p for p in attachments if p and p.exists()]
    total = sum(p.stat().st_size for p in files)
    if total > MAX_ATTACHMENT_BYTES:
        raise MailError(f"Attachments total {total} bytes, over the send limit.")

    company = deal.company_name or deck_filename
    payload = {
        "from": config.sender,
        "to": [config.recipient],
        "subject": f"Deck analysis: {company}",
        "text": _render_text(deal, objections, deck_filename, context),
        "html": _render_html(deal, objections, deck_filename, context),
        "attachments": [
            {
                "filename": p.name,
                "content": base64.b64encode(p.read_bytes()).decode("ascii"),
            }
            for p in files
        ],
    }

    import httpx

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise MailError(f"Could not reach Resend: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:300].strip() or f"HTTP {response.status_code}"
        raise MailError(f"Resend rejected the message: {detail}")

    try:
        return str(response.json().get("id", ""))
    except ValueError:
        return ""
