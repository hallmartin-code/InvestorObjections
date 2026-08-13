"""Loader for the document structure template.

The template is the single source of truth for two things: which fields are analyzed in
every deck, and how the resulting documents are laid out. It ships inside the package at
``templates/one_pager_template.json`` and can be replaced per-run by pointing the
``INVESTOR_TOOLKIT_TEMPLATE`` environment variable at another JSON file with the same shape.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = TEMPLATE_DIR / "one_pager_template.json"
EMAIL_MARKDOWN_TEMPLATE = TEMPLATE_DIR / "follow_up_emails_template.md"

EMAIL_BLOCK_START = "<!-- BEGIN EMAIL BLOCK"
EMAIL_BLOCK_END = "<!-- END EMAIL BLOCK -->"

REQUIRED_SECTIONS = ("analysis_fields", "page", "palette", "typography", "header", "body", "footer")


class TemplateError(RuntimeError):
    """Raised when the document template is missing or malformed."""


def template_path() -> Path:
    """Path to the active template, honouring the environment override."""
    override = os.environ.get("INVESTOR_TOOLKIT_TEMPLATE", "").strip()
    return Path(override) if override else DEFAULT_TEMPLATE


@lru_cache(maxsize=4)
def _load(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        raise TemplateError(f"Document template not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"Document template {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"Document template {path} must contain a JSON object.")
    missing = [key for key in REQUIRED_SECTIONS if key not in data]
    if missing:
        raise TemplateError(f"Document template {path} is missing: {', '.join(missing)}")
    return data


def load_template() -> dict:
    """The active document template as a dict."""
    return _load(str(template_path()))


def analysis_fields() -> dict[str, str]:
    """Field name to extraction guidance, for every field analyzed in a deck."""
    fields = load_template()["analysis_fields"]
    if not isinstance(fields, dict) or not fields:
        raise TemplateError("Template 'analysis_fields' must be a non-empty object.")
    return {str(k): str(v) for k, v in fields.items()}


def email_angles() -> list[dict[str, str]]:
    """The email angles defined by the template, in output order."""
    angles = load_template().get("email_set", {}).get("angles", [])
    if not angles:
        raise TemplateError("Template 'email_set.angles' must list at least one angle.")
    return [{str(k): str(v) for k, v in angle.items()} for angle in angles]


def email_titles() -> dict[str, str]:
    """Angle key to display title."""
    return {angle["key"]: angle["title"] for angle in email_angles()}


def section_fields() -> list[str]:
    """Every deal field rendered as a body section, in page order."""
    out: list[str] = []
    for column in load_template()["body"]["columns"]:
        out.extend(section["field"] for section in column["sections"])
    return out


def email_markdown_template() -> str:
    """The raw Markdown skeleton used to render the follow-up email document."""
    if not EMAIL_MARKDOWN_TEMPLATE.exists():
        raise TemplateError(f"Email Markdown template not found: {EMAIL_MARKDOWN_TEMPLATE}")
    return EMAIL_MARKDOWN_TEMPLATE.read_text(encoding="utf-8")


def split_email_markdown(text: str) -> tuple[str, str]:
    """Split the Markdown skeleton into its document header and its repeatable email block."""
    if EMAIL_BLOCK_START not in text or EMAIL_BLOCK_END not in text:
        raise TemplateError("Email Markdown template is missing its EMAIL BLOCK markers.")
    head, rest = text.split(EMAIL_BLOCK_START, 1)
    # Drop the remainder of the opening marker's comment line.
    _, block = rest.split("-->", 1)
    block = block.split(EMAIL_BLOCK_END, 1)[0]
    return head.rstrip("\n"), block.strip("\n")


def render(text: str, values: dict[str, object]) -> str:
    """Substitute ``{{placeholder}}`` tokens; unknown tokens are left untouched."""
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text
