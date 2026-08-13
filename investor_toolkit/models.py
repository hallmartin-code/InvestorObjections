"""Pydantic models for extracted deal data and the generated email set.

The set of fields analyzed in a deck and the set of email angles are declared in the document
template; the models here are the typed surface for those declarations, and
:func:`validate_against_template` checks the two stay in step.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .template import TemplateError, analysis_fields, email_titles


class DealData(BaseModel):
    """Structured deal facts extracted from a pitch deck.

    Every field defaults to an empty string: the extraction prompt instructs the model to
    return "" rather than invent content, and downstream rendering treats "" as "omit".
    """

    company_name: str = ""
    tagline: str = ""
    problem: str = ""
    solution: str = ""
    why_now: str = ""
    market_size: str = ""
    traction: str = ""
    business_model: str = ""
    team: str = ""
    ask: str = ""
    stage: str = ""
    key_risks_retired: str = ""
    competitive_moat: str = ""
    contact: str = ""
    raw_deck_text: str = Field(default="", repr=False)

    def slug(self) -> str:
        """Filesystem-safe stem for output files."""
        name = (self.company_name or "company").strip()
        safe = "".join(c if (c.isalnum() or c in " -_") else " " for c in name)
        slug = "_".join(safe.split())
        return slug or "company"


class EmailSet(BaseModel):
    """Five follow-up emails, one per 'why now' angle."""

    timing_inflection: str = ""
    de_risking_validation: str = ""
    market_structural_shift: str = ""
    capital_efficiency_ownership: str = ""
    soft_ask_reengagement: str = ""


def validate_against_template() -> None:
    """Fail loudly if the active template declares fields or angles the models cannot hold."""
    unknown_fields = [f for f in analysis_fields() if f not in DealData.model_fields]
    if unknown_fields:
        raise TemplateError(
            "Template 'analysis_fields' declares fields that DealData does not define: "
            + ", ".join(sorted(unknown_fields))
        )
    unknown_angles = [k for k in email_titles() if k not in EmailSet.model_fields]
    if unknown_angles:
        raise TemplateError(
            "Template 'email_set.angles' declares keys that EmailSet does not define: "
            + ", ".join(sorted(unknown_angles))
        )


# Angle key -> display title, sourced from the active document template.
EMAIL_TITLES: dict[str, str] = email_titles()
