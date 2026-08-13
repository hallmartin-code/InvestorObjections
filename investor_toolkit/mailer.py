"""Archive copy of every completed run, emailed through Resend.

Disabled unless all three of ``RESEND_API_KEY``, ``MAIL_FROM`` and ``RUN_COPY_TO`` are set.
A partial configuration is treated as a misconfiguration and surfaced in the UI rather than
silently ignored — a disclosure that promises an emailed copy must not quietly stop being true.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 20.0

# Resend caps a request at 40MB; our artifacts are a few hundred KB at most.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


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

    present = {"RESEND_API_KEY": api_key, "MAIL_FROM": sender, "RUN_COPY_TO": recipient}
    if not any(present.values()):
        return None
    missing = [name for name, value in present.items() if not value]
    if missing:
        raise MailError(
            "Run-copy email is partially configured; missing: " + ", ".join(missing)
        )
    return MailConfig(api_key=api_key, sender=sender, recipient=recipient)


def recipient() -> str | None:
    """The archive recipient, or None when mail is off or misconfigured."""
    try:
        config = load_config()
    except MailError:
        return None
    return config.recipient if config else None


def send_run_copy(
    *,
    company: str,
    deck_filename: str,
    context: str,
    attachments: list[Path],
    config: MailConfig | None = None,
) -> str:
    """Email the run's artifacts to the configured archive address.

    Returns the Resend message id.

    Raises:
        MailError: mail is off, misconfigured, the payload is too large, or Resend refused.
    """
    config = config or load_config()
    if config is None:
        raise MailError("Run-copy email is not configured.")

    files = [p for p in attachments if p and p.exists()]
    if not files:
        raise MailError("No artifacts to attach.")

    total = sum(p.stat().st_size for p in files)
    if total > MAX_ATTACHMENT_BYTES:
        raise MailError(f"Attachments total {total} bytes, over the send limit.")

    body = (
        f"Investor Toolkit run copy\n\n"
        f"Company: {company or 'Unknown'}\n"
        f"Source deck: {deck_filename}\n"
        f"Interaction context: {context}\n\n"
        f"Attached: {', '.join(p.name for p in files)}\n\n"
        f"Confidential — For Accredited Investors Only\n"
    )

    payload = {
        "from": config.sender,
        "to": [config.recipient],
        "subject": f"{company or deck_filename} — investor one-pager",
        "text": body,
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
