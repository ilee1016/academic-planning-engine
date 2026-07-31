"""Provider protocol and concrete provider implementations.

Defines the `ExplanationProvider` protocol and the optional Anthropic
concrete provider.  Contains no FastAPI route logic.

Public API:
    ExplanationProvider  (Protocol)
    AnthropicProvider    (concrete; requires anthropic SDK)
    load_provider(provider_name, api_key, model, timeout_seconds)

Provider selection (EXPLANATION_PROVIDER env var, read by main.py):
    "none"      — no provider; ExplanationService returns deterministic fallback
    "anthropic" — AnthropicProvider using claude-haiku-4-5-20251001

Concrete provider details (AnthropicProvider):
    SDK:             anthropic >= 0.30 (AsyncAnthropic client)
    Model default:   claude-haiku-4-5-20251001
    Max tokens:      300  (~140 words; validated downstream)
    Timeout:         configurable, default 8 s (enforced by ExplanationService)
    Failure mode:    propagates exception; ExplanationService catches and falls back.

Invariants:
    INV-AI-01  Provider receives only ExplainerInput — no StudentRecord or audit text.
    INV-AI-02  Provider prompt instructions are in the system message; all DTO
               values are in the user message so they are treated as data, not
               instructions (prompt-injection boundary).
    INV-AI-09  Provider calls occur only through ExplanationService, never inline
               in route handlers.
"""
from __future__ import annotations

import logging
from typing import Protocol

from app.core.explainer import ExplainerInput

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — instructions only, no untrusted data
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are explaining a schedule produced by a deterministic academic planning engine. "
    "You may only describe the supplied schedule and deterministic metadata. "
    "Do not add, remove, recommend, or modify courses. "
    "Do not claim that a requirement is completed; "
    "say the schedule addresses or contributes toward it. "
    "Do not infer workload, professor quality, seat availability, prerequisites, "
    "graduation status, or student identity. "
    "Write 60–140 words of plain prose. "
    "No headings, no bullet points, no Markdown tables. "
    "Do not use the words ‘completes’, ‘fulfills’, ‘finishes’, "
    "‘guarantees graduation’, or ‘ensures graduation’."
)

_PROMPT_VERSION = "1"

# Day abbreviation → full name for human-readable provider messages.
_DAY_NAMES: dict[str, str] = {
    "M": "Monday",
    "T": "Tuesday",
    "W": "Wednesday",
    "R": "Thursday",
    "F": "Friday",
}

_COMPONENT_LABELS: dict[str, str] = {
    "requirement_gains": "Requirement coverage",
    "preferred_subjects": "Preferred subject alignment",
    "free_days": "Incidental free days",
    "compactness": "Compact weekly layout",
    "credit_load": "Credit load proximity to target",
}


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class ExplanationProvider(Protocol):
    """Protocol for AI explanation providers.

    The provider receives only a sanitized ExplainerInput and returns plain
    text.  It must never receive StudentRecord, RequirementStatus, raw audit
    content, session metadata, or scores in unstructured form.

    Timeout handling is the caller's responsibility (ExplanationService wraps
    calls in asyncio.wait_for).
    """

    async def explain(self, explainer_input: ExplainerInput) -> str:
        """Return an explanation for the given schedule input.

        Raises on timeout or network error so the caller can fall back.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Explanation provider using the Anthropic claude-haiku model.

    The system message contains only instructions; all DTO values appear in
    the user message and are treated as structured data, not instructions.
    This establishes the prompt-injection boundary.
    """

    PROVIDER_NAME = "anthropic"
    PROMPT_VERSION = _PROMPT_VERSION

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        timeout_seconds: float = 8.0,
    ) -> None:
        from anthropic import AsyncAnthropic  # imported lazily so tests don't require it

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        # timeout_seconds is stored for reference; timeout is enforced by the service.
        self._timeout_seconds = timeout_seconds

    async def explain(self, explainer_input: ExplainerInput) -> str:
        """Call the Anthropic API and return explanation text."""
        user_message = _build_provider_message(explainer_input)

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        block = response.content[0]
        if hasattr(block, "text"):
            return str(block.text)
        raise ValueError("Unexpected provider response structure: no text block")


# ---------------------------------------------------------------------------
# Provider message builder — untrusted data separated from instructions
# ---------------------------------------------------------------------------


def _build_provider_message(inp: ExplainerInput) -> str:
    """Build the structured data message sent to the provider.

    All DTO field values are treated as DATA.  System-level instructions are
    in _SYSTEM_PROMPT, not here.  This separation is the prompt-injection
    boundary: if a course title contains adversarial text, it appears only as
    a data value and the validation step will reject any output that acts on it.
    """
    lines: list[str] = ["Schedule information:"]
    lines.append(f"Category: {inp.category}")
    lines.append(f"Total credits: {inp.total_credits}")

    lines.append("\nCourses in this schedule:")
    for section in inp.sections:
        kind = "linked section" if section.is_linked_child else "course"
        mt_strs = [
            f"{''.join(mt.days)} {mt.start.strftime('%H:%M')}-{mt.end.strftime('%H:%M')}"
            for mt in section.meeting_times
        ]
        times = ", ".join(mt_strs) if mt_strs else "times TBD"
        lines.append(
            f"  {section.course_code} — {section.title} "
            f"({kind}, {section.credits} cr, {times})"
        )

    if inp.requirement_gains:
        lines.append("\nRequirement categories this schedule addresses:")
        for gain in inp.requirement_gains:
            lines.append(f"  - {gain.label}")
    else:
        lines.append(
            "\nThis schedule does not address any tracked requirement categories."
        )

    lines.append("\nScore breakdown:")
    for key, val in inp.score_breakdown:
        label = _COMPONENT_LABELS.get(key, key)
        lines.append(f"  {label}: {val:.1f}")

    if inp.free_days:
        day_names = [_DAY_NAMES.get(d, d) for d in inp.free_days]
        lines.append(f"\nRequested free days: {', '.join(day_names)}")

    if inp.solver_cap_reached:
        lines.append(
            "\nNote: Schedule generation reached its result cap. "
            "This schedule was among the highest-ranked generated, "
            "not guaranteed to be globally optimal."
        )

    lines.append(
        "\nPlease write a brief explanation (60–140 words) of this schedule. "
        "Describe what it includes, what requirement categories it addresses, "
        "and why it scores well. "
        "Do not claim any requirement is completed, fulfilled, or finished."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider factory — called from main.py with env-var values
# ---------------------------------------------------------------------------


def load_provider(
    provider_name: str,
    api_key: str | None,
    model: str,
    timeout_seconds: float,
) -> AnthropicProvider | None:
    """Create a concrete provider from configuration, or return None.

    None means "use deterministic fallback only."  This function is called
    from main.py, which is the only module that reads environment variables.
    """
    name = provider_name.strip().lower()

    if name in ("none", ""):
        return None

    if name == "anthropic":
        if not api_key:
            logger.warning(
                "explanation_provider_unavailable: ANTHROPIC_API_KEY is not set; "
                "falling back to deterministic explanation"
            )
            return None
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    logger.warning(
        "explanation_provider_unavailable: unknown provider %r; "
        "falling back to deterministic explanation",
        name,
    )
    return None
