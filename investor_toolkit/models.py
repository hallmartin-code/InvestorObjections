"""Pydantic models for extracted deal data and the generated objection set.

The set of fields analyzed in a deck and the set of investor objections answered are declared
in the document template; the models here are the typed surface for those declarations, and
:func:`validate_against_template` checks the two stay in step.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .template import TemplateError, analysis_fields, objection_titles


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


class Objection(BaseModel):
    """One investor objection and the rebuttal to it."""

    objection: str = ""
    rebuttal: str = ""


class ObjectionSet(BaseModel):
    """The ten investor objections, one per category declared in the template."""

    too_early: Objection = Field(default_factory=Objection)
    market_size: Objection = Field(default_factory=Objection)
    competition: Objection = Field(default_factory=Objection)
    differentiation_moat: Objection = Field(default_factory=Objection)
    team_gaps: Objection = Field(default_factory=Objection)
    regulatory_path: Objection = Field(default_factory=Objection)
    capital_intensity: Objection = Field(default_factory=Objection)
    commercial_adoption: Objection = Field(default_factory=Objection)
    valuation_terms: Objection = Field(default_factory=Objection)
    exit_path: Objection = Field(default_factory=Objection)


def validate_against_template() -> None:
    """Fail loudly if the active template declares fields or objections the models can't hold."""
    unknown_fields = [f for f in analysis_fields() if f not in DealData.model_fields]
    if unknown_fields:
        raise TemplateError(
            "Template 'analysis_fields' declares fields that DealData does not define: "
            + ", ".join(sorted(unknown_fields))
        )
    unknown = [k for k in objection_titles() if k not in ObjectionSet.model_fields]
    if unknown:
        raise TemplateError(
            "Template 'objection_set.objections' declares keys that ObjectionSet does not "
            "define: " + ", ".join(sorted(unknown))
        )


# Objection key -> display title, sourced from the active document template.
OBJECTION_TITLES: dict[str, str] = objection_titles()
