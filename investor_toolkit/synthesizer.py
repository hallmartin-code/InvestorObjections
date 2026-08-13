"""Deal data extraction: turn raw deck text into a structured DealData via Claude.

Also holds the shared Claude client and the structured-output call used by
:mod:`investor_toolkit.email_generator`.
"""

from __future__ import annotations

import json
import os

from .models import DealData, validate_against_template
from .template import analysis_fields

MODEL = "claude-opus-5"

# Claude Opus 5 has a 1M-token context window. Cap the deck text well below it so the
# prompt, schema, and response always fit; longer decks are truncated from the tail.
MAX_DECK_CHARS = 400_000

# Opus 5's safety classifiers can decline a request (HTTP 200 with stop_reason
# "refusal"). Server-side fallbacks re-run a declined request on Anthropic's
# recommended substitute model inside the same call, routed by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = (
    "You are a senior investment analyst at an early-stage venture firm. "
    "Extract factual deal data only. Do not hallucinate. If a field is not present in the "
    "source material, return an empty string. Do not infer or embellish."
)

# Fields the model is asked to fill, declared by the document template. Each maps to a
# DealData field; the raw deck text is carried separately and never requested from the model.
validate_against_template()
EXTRACTED_FIELDS: dict[str, str] = analysis_fields()


class SynthesisError(RuntimeError):
    """Raised when deal extraction fails."""


def get_client():
    """Build an Anthropic client, failing clearly if the API key is absent."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SynthesisError("ANTHROPIC_API_KEY not set. Add it to your .env file.")
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def string_schema(fields: dict[str, str]) -> dict:
    """A closed JSON schema whose properties are all required strings."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            field: {"type": "string", "description": desc} for field, desc in fields.items()
        },
        # Structured outputs require every property to be listed as required; the system
        # prompt is what makes unknown fields come back as "".
        "required": list(fields),
    }


def call_structured(
    *,
    system: str,
    user_prompt: str,
    schema: dict,
    effort: str,
    max_tokens: int,
    error: type[Exception],
    label: str,
) -> dict:
    """Run one structured-output Claude call and return the parsed JSON object.

    Streams the response so a long request cannot hit an HTTP read timeout, and raises
    ``error`` with an actionable message on any failure.
    """
    client = get_client()

    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:  # surface the raw API error to the caller
        raise error(f"Claude API error during {label}: {exc}") from exc

    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) or "unspecified"
        raise error(
            f"Claude declined the {label} request (refusal category: {category}). "
            "The fallback model declined it as well."
        )
    if message.stop_reason == "max_tokens":
        raise error(
            f"Claude hit the output limit during {label} and the response was truncated. "
            f"Raise max_tokens (currently {max_tokens})."
        )

    # output_config.format guarantees the response text is valid JSON matching the schema.
    # Thinking blocks may precede it, so select the text block explicitly.
    content = next((b.text for b in message.content if b.type == "text"), "").strip()
    if not content:
        raise error(f"Claude returned an empty response for {label}.")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise error(f"Could not parse the {label} JSON from the model response: {exc}") from exc

    if not isinstance(payload, dict):
        raise error(f"Model returned JSON that was not an object for {label}.")
    return payload


def extract_deal_data(deck_text: str) -> DealData:
    """Extract structured deal facts from raw deck text.

    Raises:
        SynthesisError: on a missing API key, an API failure, or an unparseable response.
    """
    if not deck_text or not deck_text.strip():
        raise SynthesisError("The deck contained no extractable text.")

    text = deck_text.strip()
    if len(text) > MAX_DECK_CHARS:
        text = text[:MAX_DECK_CHARS] + "\n\n[deck text truncated]"

    user_prompt = (
        "Extract the deal data from the pitch deck text below. Slides are separated by "
        "'---'. Return an empty string for any field the deck does not state.\n\n"
        "PITCH DECK TEXT:\n"
        f"{text}"
    )

    payload = call_structured(
        system=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=string_schema(EXTRACTED_FIELDS),
        effort="medium",  # literal extraction: depth buys little here
        max_tokens=8000,
        error=SynthesisError,
        label="deal extraction",
    )

    # Keep only known fields, coerce nulls to "", and attach the untruncated deck text.
    data = {k: (payload.get(k) or "") for k in EXTRACTED_FIELDS}
    data = {k: (v if isinstance(v, str) else str(v)).strip() for k, v in data.items()}
    data["raw_deck_text"] = deck_text

    try:
        return DealData(**data)
    except Exception as exc:
        raise SynthesisError(f"Model response did not match the DealData schema: {exc}") from exc
