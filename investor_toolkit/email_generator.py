"""Five investor follow-up emails, generated in a single Claude call."""

from __future__ import annotations

from .models import EMAIL_TITLES, DealData, EmailSet
from .synthesizer import call_structured, string_schema
from .template import email_markdown_template, render, split_email_markdown

SYSTEM_PROMPT = (
    "You are an experienced founder writing follow-up emails to sophisticated angel "
    "investors, family offices, and early-stage funds. You write concise, analytical emails "
    "that investors actually read and forward. Your emails are calm, confident, and factual "
    "— never hyped, never urgent. You respect the investor's time."
)

USER_PROMPT_TEMPLATE = """Generate exactly 5 investor follow-up emails for the following deal. \
Return a JSON object with keys: timing_inflection, de_risking_validation, \
market_structural_shift, capital_efficiency_ownership, soft_ask_reengagement.

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

EMAIL REQUIREMENTS (apply to ALL five emails):
- 90-140 words each
- 4-6 short paragraphs, no bullet points, no headers inside the email body
- Tone: calm, confident, factual - no hype, no urgency theatrics
- No phrases like: "excited", "huge opportunity", "game-changing"
- Do not ask directly for money
- Do not mention valuation unless it was explicitly provided above
- No emojis, no sales language
- Each email must stand alone (no references like "as mentioned above")
- Each must be forwardable internally by an investment partner
- Each must include one specific reason this opportunity is timely right now
- End with a simple, professional sign-off in founder voice (not "the team")

EMAIL 1 - timing_inflection:
Focus on near-term data, launch, or regulatory inflection. Why this moment is pre-repricing. \
Goal: Spark internal discussion.

EMAIL 2 - de_risking_validation:
Focus on what risks are already retired and external validation (customers, regulators, \
partners, non-dilutive funding). Goal: Reduce "too early" objections.

EMAIL 3 - market_structural_shift:
Focus on a recent change in market behavior, regulation, infrastructure, or cost curves. \
Why older versions of this category failed but this one won't. Goal: Reframe historical \
skepticism.

EMAIL 4 - capital_efficiency_ownership:
Focus on why capital today goes toward value creation, not discovery. How early capital \
compounds (optionality, pipeline, platform leverage). Goal: Appeal to disciplined angels and \
family offices.

EMAIL 5 - soft_ask_reengagement:
Focus on relevance, not pressure. Invite a next step without assuming interest. Goal: Keep \
the conversation alive.

QUALITY CHECK: Before finalizing, verify each email passes this test - "Would a partner feel \
comfortable forwarding this internally?" Remove anything that sounds like a pitch deck slide. \
Prioritize clarity over completeness.

Return ONLY the JSON object. No preamble, no explanation."""


class EmailGenerationError(RuntimeError):
    """Raised when email generation fails."""


def _json_schema() -> dict:
    return string_schema(
        {key: f"Follow-up email: {title}." for key, title in EMAIL_TITLES.items()}
    )


def generate_emails(deal: DealData, interaction_context: str) -> EmailSet:
    """Generate the five follow-up emails for ``deal``.

    Args:
        deal: extracted deal facts.
        interaction_context: how the investor was met, e.g. "intro via mutual LP".

    Raises:
        EmailGenerationError: on a missing API key, an API failure, or a bad response.
    """
    context = (interaction_context or "initial outreach").strip()

    def value(field: str) -> str:
        return (getattr(deal, field, "") or "").strip() or "Not provided"

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
    )

    payload = call_structured(
        system=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_json_schema(),
        effort="high",  # writing quality is the whole deliverable here
        max_tokens=8000,
        error=EmailGenerationError,
        label="email generation",
    )

    missing = [k for k in EMAIL_TITLES if not str(payload.get(k) or "").strip()]
    if missing:
        raise EmailGenerationError(
            "Model response was missing these emails: " + ", ".join(missing)
        )

    return EmailSet(**{k: str(payload[k]).strip() for k in EMAIL_TITLES})


def emails_to_markdown(deal: DealData, emails: EmailSet, interaction_context: str) -> str:
    """Render the email set into the Markdown structure template."""
    head, block = split_email_markdown(email_markdown_template())

    document = render(
        head,
        {
            "company_name": deal.company_name or "Company",
            "interaction_context": interaction_context or "initial outreach",
            "stage": deal.stage or "Not stated",
        },
    )

    parts = [document.rstrip("\n"), ""]
    for index, (key, title) in enumerate(EMAIL_TITLES.items(), start=1):
        body = getattr(emails, key).strip()
        parts.append(
            render(
                block,
                {
                    "email_number": index,
                    "email_title": title,
                    "word_count": len(body.split()),
                    "email_body": body,
                },
            ).strip()
        )
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_template_skeleton() -> str:
    """The empty Markdown structure — headers and ``{{placeholder}}`` tokens only."""
    head, block = split_email_markdown(email_markdown_template())
    parts = [head.rstrip("\n"), ""]
    for index, (key, title) in enumerate(EMAIL_TITLES.items(), start=1):
        parts.append(
            render(
                block,
                {
                    "email_number": index,
                    "email_title": title,
                    "email_body": "{{" + key + "}}",
                },
            ).strip()
        )
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def emails_to_list(emails: EmailSet) -> list[dict]:
    """The email set as ``{number, title, body, words}`` dicts, in template order."""
    return [
        {
            "number": index,
            "title": title,
            "body": getattr(emails, key),
            "words": len(getattr(emails, key).split()),
        }
        for index, (key, title) in enumerate(EMAIL_TITLES.items(), start=1)
    ]
