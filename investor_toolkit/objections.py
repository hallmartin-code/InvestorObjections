"""Ten investor objections and their rebuttals, generated in a single Claude call.

The objection categories come from the document template, so adding, reordering or rewording
one is a template edit. Rebuttals are held to the same standard as the rest of the toolkit:
grounded in the deck, and explicit about what is missing rather than inventing support.
"""

from __future__ import annotations

import re

from .models import OBJECTION_TITLES, DealData, Objection, ObjectionSet
from .synthesizer import call_structured
from .template import (
    objection_markdown_template,
    objection_word_targets,
    objections,
    render,
    split_objection_markdown,
)

SYSTEM_PROMPT = (
    "You are a seasoned early-stage investor preparing a partner for a founder meeting. "
    "You state objections in the sharp, specific form a partner would actually voice them in "
    "an investment committee, and you answer them only with evidence that is present in the "
    "deal materials. You never invent facts, figures, or endorsements. Where the materials do "
    "not support a rebuttal, you say so plainly and name the evidence that would settle it."
)

USER_PROMPT_TEMPLATE = """Generate the ten investor objections below for the following deal, \
each with a rebuttal. Return a JSON object whose keys are exactly the objection keys listed, \
each mapping to an object with "objection" and "rebuttal" string fields.

DEAL CONTEXT:
Company: {company_name}
Stage: {stage}
Tagline: {tagline}
Problem: {problem}
Solution: {solution}
Why Now: {why_now}
Market Size: {market_size}
Traction: {traction}
Business Model: {business_model}
Team: {team}
Ask: {ask}
Key Risks Retired: {key_risks_retired}
Competitive Moat: {competitive_moat}
Interaction Context: {interaction_context}

OBJECTIONS TO COVER:
{objection_spec}

REQUIREMENTS (apply to ALL ten):
- objection: {objection_words} words, written in the investor's own voice, first person
- The objection must be specific to THIS deal - name the actual gap, figure, or claim it \
targets. Never a generic template objection that would apply to any company.
- rebuttal: {rebuttal_words} words, answering that objection directly
- Ground every rebuttal in the deal context above. Cite the specific figures, names, dates, \
and evidence it gives you.
- Do NOT invent facts, data, customers, endorsements, or comparables. If the deal context \
does not support a rebuttal, say so in the rebuttal itself and state precisely what evidence \
would resolve the objection. A candid "the deck does not establish this yet, and here is what \
would" is more useful than a confident answer that is not supported.
- Tone: calm, factual, direct. No hype, no defensiveness, no sales language.
- Do not mention valuation figures unless they were explicitly provided above.
- Each objection and rebuttal must stand alone - no references like "as noted above".
- No emojis, no headers or bullet points inside either field.

QUALITY CHECK: before finalising, verify each rebuttal against the deal context line by line. \
If a claim in a rebuttal is not traceable to something above, remove it or replace it with the \
honest statement of what is missing.

Return ONLY the JSON object. No preamble, no explanation."""


def _clean(text: str) -> str:
    """Normalise model text before it reaches a PDF, an email, or a Markdown file.

    Occasionally the model wraps a clause in stray triple quotes; those survive JSON
    decoding intact and read as corruption in the rendered output.
    """
    text = re.sub(r'"{3,}', " ", str(text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ObjectionGenerationError(RuntimeError):
    """Raised when objection generation fails."""


def _json_schema() -> dict:
    """Closed schema: one object per objection key, each with objection + rebuttal."""

    def pair(title: str) -> dict:
        return {
            "type": "object",
            "description": f"Objection: {title}.",
            "additionalProperties": False,
            "properties": {
                "objection": {
                    "type": "string",
                    "description": "The objection, in the investor's voice.",
                },
                "rebuttal": {
                    "type": "string",
                    "description": "The grounded answer to that objection.",
                },
            },
            "required": ["objection", "rebuttal"],
        }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: pair(title) for key, title in OBJECTION_TITLES.items()},
        "required": list(OBJECTION_TITLES),
    }


def generate_objections(deal: DealData, interaction_context: str) -> ObjectionSet:
    """Generate the ten objections and rebuttals for ``deal``.

    Raises:
        ObjectionGenerationError: on a missing API key, an API failure, or a bad response.
    """
    context = (interaction_context or "initial outreach").strip()
    objection_words, rebuttal_words = objection_word_targets()

    def value(field: str) -> str:
        return (getattr(deal, field, "") or "").strip() or "Not provided"

    spec = "\n".join(
        f"- {item['key']} ({item['title']}): {item['focus']} "
        f"A strong rebuttal must: {item['rebuttal_must']}"
        for item in objections()
    )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        company_name=value("company_name"),
        stage=value("stage"),
        tagline=value("tagline"),
        problem=value("problem"),
        solution=value("solution"),
        why_now=value("why_now"),
        market_size=value("market_size"),
        traction=value("traction"),
        business_model=value("business_model"),
        team=value("team"),
        ask=value("ask"),
        key_risks_retired=value("key_risks_retired"),
        competitive_moat=value("competitive_moat"),
        interaction_context=context,
        objection_spec=spec,
        objection_words=objection_words,
        rebuttal_words=rebuttal_words,
    )

    payload = call_structured(
        system=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_json_schema(),
        effort="high",  # judging what the deck does and does not support is the actual work
        max_tokens=16000,
        error=ObjectionGenerationError,
        label="objection generation",
    )

    missing = [
        key
        for key in OBJECTION_TITLES
        if not isinstance(payload.get(key), dict)
        or not str(payload[key].get("objection") or "").strip()
        or not str(payload[key].get("rebuttal") or "").strip()
    ]
    if missing:
        raise ObjectionGenerationError(
            "Model response was missing these objections: " + ", ".join(missing)
        )

    return ObjectionSet(
        **{
            key: Objection(
                objection=_clean(payload[key]["objection"]),
                rebuttal=_clean(payload[key]["rebuttal"]),
            )
            for key in OBJECTION_TITLES
        }
    )


def objections_to_list(items: ObjectionSet) -> list[dict]:
    """The objection set as render-ready dicts, in template order."""
    out = []
    for index, (key, title) in enumerate(OBJECTION_TITLES.items(), start=1):
        entry: Objection = getattr(items, key)
        out.append(
            {
                "number": index,
                "key": key,
                "title": title,
                "objection": entry.objection,
                "rebuttal": entry.rebuttal,
                "words": len(entry.rebuttal.split()),
            }
        )
    return out


def objections_to_markdown(deal: DealData, items: ObjectionSet, interaction_context: str) -> str:
    """Render the objection set into the Markdown structure template."""
    head, block = split_objection_markdown(objection_markdown_template())

    document = render(
        head,
        {
            "company_name": deal.company_name or "Company",
            "interaction_context": interaction_context or "initial outreach",
            "stage": deal.stage or "Not stated",
        },
    )

    parts = [document.rstrip("\n"), ""]
    for item in objections_to_list(items):
        parts.append(
            render(
                block,
                {
                    "objection_number": item["number"],
                    "objection_title": item["title"],
                    "objection": item["objection"],
                    "rebuttal": item["rebuttal"],
                },
            ).strip()
        )
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_template_skeleton() -> str:
    """The empty Markdown structure — headings and ``{{placeholder}}`` tokens only."""
    head, block = split_objection_markdown(objection_markdown_template())
    parts = [head.rstrip("\n"), ""]
    for index, (key, title) in enumerate(OBJECTION_TITLES.items(), start=1):
        parts.append(
            render(
                block,
                {
                    "objection_number": index,
                    "objection_title": title,
                    "objection": "{{" + key + ".objection}}",
                    "rebuttal": "{{" + key + ".rebuttal}}",
                },
            ).strip()
        )
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
