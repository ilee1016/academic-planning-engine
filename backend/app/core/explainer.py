"""Sanitized explainer DTO, deterministic fallback, and output validation.

This module is the exact boundary between the deterministic engine and the
generative AI layer.  It has no network calls, no provider imports, no
FastAPI or Pydantic dependencies, and never reads environment variables.

Public API:
    ExplainerSection
    ExplainerRequirementGain
    ExplainerInput
    ExplanationValidationError
    build_explainer_input(ranked_schedule, preferences, *, schedule_id, solver_cap_reached)
    generate_fallback_explanation(explainer_input)
    validate_explanation(text, explainer_input)

Invariants:
    INV-01   core/ never imports from adapters/ or from provider modules.
    INV-AI-01  All ExplainerInput fields are schedule-derived; no student identity.
    INV-AI-03  AI may not modify deterministic outputs.
    INV-AI-05  Fallback output is deterministic: identical inputs → identical output.
    INV-AI-06  Unknown course codes in provider output are forbidden.
    INV-AI-07  Requirement gains must not be described as guaranteed completion.
    INV-AI-08  Schedule generation never depends on this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.models import MeetingTime, Preferences, RankedSchedule

# ---------------------------------------------------------------------------
# Explainer sub-DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExplainerSection:
    """One course section as seen by the AI explanation layer.

    Instructor names are deliberately omitted — they are not needed for the
    explanation and reduce the privacy surface area of provider payloads.
    """

    course_code: str
    title: str
    credits: Decimal
    meeting_times: tuple[MeetingTime, ...]
    is_linked_child: bool


@dataclass(frozen=True)
class ExplainerRequirementGain:
    """One requirement category that this schedule would address."""

    id: str
    label: str


# ---------------------------------------------------------------------------
# Primary sanitized DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExplainerInput:
    """Sanitized, immutable data transfer object for the AI explanation layer.

    Contains only schedule-derived fields.  No student name, student ID,
    session ID, raw audit text, file bytes, or any other personally
    identifying information is present.

    score_breakdown is a tuple of (key, value) pairs in canonical order
    matching _BREAKDOWN_KEY_ORDER.
    """

    schedule_id: str
    sections: tuple[ExplainerSection, ...]
    total_credits: Decimal
    requirement_gains: tuple[ExplainerRequirementGain, ...]
    score: float
    score_breakdown: tuple[tuple[str, float], ...]
    category: str
    free_days: tuple[str, ...]
    preferred_subjects: tuple[str, ...]
    solver_cap_reached: bool


# Canonical order for score breakdown keys — must match core/ranking.py.
_BREAKDOWN_KEY_ORDER: tuple[str, ...] = (
    "requirement_gains",
    "preferred_subjects",
    "free_days",
    "compactness",
    "credit_load",
)


# ---------------------------------------------------------------------------
# Explainer input construction
# ---------------------------------------------------------------------------


def build_explainer_input(
    ranked_schedule: RankedSchedule,
    preferences: Preferences,
    *,
    schedule_id: str,
    solver_cap_reached: bool,
) -> ExplainerInput:
    """Build a sanitized ExplainerInput from a RankedSchedule and Preferences.

    Never mutates ranked_schedule or preferences.
    Never includes student identity (name, ID, session ID, audit text).
    Preserves Decimal credit values exactly.
    Preserves deterministic section and requirement-gain ordering.
    Parent sections appear before linked (lab/drill) sections.
    """
    sched = ranked_schedule.schedule

    # Parent sections first, then linked (lab/drill) sections.
    sections: list[ExplainerSection] = [
        ExplainerSection(
            course_code=s.course_code,
            title=s.title,
            credits=s.credits,
            meeting_times=tuple(s.meeting_times),
            is_linked_child=False,
        )
        for s in sched.parent_sections
    ]
    sections += [
        ExplainerSection(
            course_code=s.course_code,
            title=s.title,
            credits=s.credits,
            meeting_times=tuple(s.meeting_times),
            is_linked_child=True,
        )
        for s in sched.lab_sections
    ]

    # Requirement gains in ranked-schedule order.
    gains = tuple(
        ExplainerRequirementGain(id=item.id, label=item.label)
        for item in ranked_schedule.requirement_gains
    )

    # Score breakdown in canonical key order; fill 0.0 for any missing keys.
    bd = ranked_schedule.score_breakdown
    breakdown: tuple[tuple[str, float], ...] = tuple(
        (k, bd.get(k, 0.0)) for k in _BREAKDOWN_KEY_ORDER
    )

    return ExplainerInput(
        schedule_id=schedule_id,
        sections=tuple(sections),
        total_credits=sched.total_credits,
        requirement_gains=gains,
        score=ranked_schedule.score,
        score_breakdown=breakdown,
        category=ranked_schedule.category,
        free_days=tuple(preferences.free_days),
        preferred_subjects=tuple(preferences.preferred_subjects),
        solver_cap_reached=solver_cap_reached,
    )


# ---------------------------------------------------------------------------
# Human-readable translation tables
# ---------------------------------------------------------------------------

_DAY_NAMES: dict[str, str] = {
    "M": "Monday",
    "T": "Tuesday",
    "W": "Wednesday",
    "R": "Thursday",
    "F": "Friday",
}

_SCORE_COMPONENT_LABELS: dict[str, str] = {
    "requirement_gains": "requirement coverage",
    "preferred_subjects": "preferred subject alignment",
    "free_days": "incidental free days",
    "compactness": "compact weekly layout",
    "credit_load": "credit load proximity to target",
}


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _credits_str(credits: Decimal) -> str:
    """Format a Decimal credit value as a human-readable string."""
    if credits == Decimal("1"):
        return "1 credit"
    # Normalize removes trailing zeros: Decimal("3.0") → Decimal("3").
    val = credits.normalize()
    return f"{val} credits"


def _day_name(day: str) -> str:
    return _DAY_NAMES.get(day.upper(), day)


# ---------------------------------------------------------------------------
# Deterministic fallback explanation
# ---------------------------------------------------------------------------


def generate_fallback_explanation(explainer_input: ExplainerInput) -> str:
    """Generate a deterministic, factual explanation from ExplainerInput fields.

    Rules enforced here:
    - Uses "addresses" or "contributes toward", never "completes" or "fulfills".
    - Never includes student identity or raw score values.
    - Deterministic: identical inputs always produce identical output.
    - Target length: approximately 50–130 words.
    """
    inp = explainer_input

    parent_count = sum(1 for s in inp.sections if not s.is_linked_child)
    linked_count = sum(1 for s in inp.sections if s.is_linked_child)

    # Sentence 1: structural overview.
    s1 = (
        f"This schedule includes {_credits_str(inp.total_credits)} across "
        f"{parent_count} {_plural(parent_count, 'course', 'courses')}"
    )
    if linked_count == 1:
        s1 += " and 1 linked lab"
    elif linked_count > 1:
        s1 += f" and {linked_count} linked sections"
    s1 += "."
    sentences = [s1]

    # Sentence 2: requirement gains (when present).
    if inp.requirement_gains:
        labels = [g.label for g in inp.requirement_gains]
        if len(labels) == 1:
            sentences.append(f"It addresses the {labels[0]} category.")
        elif len(labels) == 2:
            sentences.append(
                f"It contributes toward the {labels[0]} and {labels[1]} categories."
            )
        else:
            joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
            sentences.append(f"It contributes toward the {joined} categories.")

    # Sentence 3: top scoring components (up to 2 nonzero, highest first).
    ranked_comps = sorted(
        [(k, v) for k, v in inp.score_breakdown if v > 0],
        key=lambda x: -x[1],
    )[:2]
    if ranked_comps:
        comp_labels = [
            _SCORE_COMPONENT_LABELS.get(k, k.replace("_", " "))
            for k, _ in ranked_comps
        ]
        if len(comp_labels) == 1:
            sentences.append(f"It ranks well for {comp_labels[0]}.")
        else:
            sentences.append(
                f"It ranks well for {comp_labels[0]} and {comp_labels[1]}."
            )

    # Sentence 4: explicitly requested free days.
    if inp.free_days:
        day_names = [_day_name(d) for d in inp.free_days]
        if len(day_names) == 1:
            sentences.append(f"{day_names[0]} remains free.")
        elif len(day_names) == 2:
            sentences.append(f"{day_names[0]} and {day_names[1]} remain free.")
        else:
            joined = ", ".join(day_names[:-1]) + f", and {day_names[-1]}"
            sentences.append(f"{joined} remain free.")

    # Sentence 5: solver cap disclosure.
    if inp.solver_cap_reached:
        sentences.append(
            "Because schedule generation reached its result cap, this is one of the "
            "highest-ranked generated schedules rather than a guaranteed global optimum."
        )

    return " ".join(sentences)


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------


class ExplanationValidationError(ValueError):
    """Raised when provider output fails content or safety validation."""


# Detects uppercase course codes in the form "SUBJ NNN" or "SUBJ NNN[A-Z]".
# Applied to original text (uppercase only) so that lowercase words like
# "meets", "ranks", etc. do not produce false positives.
_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s?(\d{3}[A-Z]?)\b")

# Prohibited completion/fulfillment language around requirements.
_COMPLETION_RE = re.compile(
    r"\b("
    r"complet(?:es?|ed|ing)\s+(?:\w+\s+)*requirement|"
    r"fulfill(?:s|ed|ing)\s+(?:\w+\s+)*requirement|"
    r"finish(?:es|ed|ing)\s+(?:\w+\s+)*requirement|"
    r"guarantees?\s+completion|"
    r"ensures?\s+graduation|"
    r"guarantee[sd]?\s+graduation"
    r")\b",
    re.IGNORECASE,
)

# A Markdown table row starts with "|".
_MARKDOWN_TABLE_RE = re.compile(r"^\|.*\|", re.MULTILINE)

# HTTP/HTTPS URLs.
_URL_RE = re.compile(r"https?://\S+")

# Common LLM refusal phrases.
_REFUSAL_RE = re.compile(
    r"\b(I cannot|I'm unable|I am unable|I can't|cannot provide|not able to provide)\b",
    re.IGNORECASE,
)

# Non-printable control characters (excludes \t, \n, \r which are harmless).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Synthetic fixture student ID pattern for privacy regression.
_STUDENT_ID_RE = re.compile(r"\b0{9}\b")

_MAX_EXPLANATION_CHARS = 1_500


def validate_explanation(
    text: str,
    explainer_input: ExplainerInput,
) -> str:
    """Validate and normalize provider-supplied explanation text.

    Returns the stripped, validated text on success.
    Raises ExplanationValidationError on any violation.

    Does NOT mutate explainer_input.
    """
    # Normalize: strip leading/trailing whitespace.
    text = text.strip()

    # 1. Non-empty.
    if not text:
        raise ExplanationValidationError("Explanation is empty after stripping")

    # 2. Maximum character length.
    if len(text) > _MAX_EXPLANATION_CHARS:
        raise ExplanationValidationError(
            f"Explanation too long: {len(text)} characters > {_MAX_EXPLANATION_CHARS}"
        )

    # 3. Disallowed control characters.
    if _CONTROL_RE.search(text):
        raise ExplanationValidationError(
            "Explanation contains disallowed control characters"
        )

    # 4. Markdown table.
    if _MARKDOWN_TABLE_RE.search(text):
        raise ExplanationValidationError("Explanation contains a Markdown table")

    # 5. URLs.
    if _URL_RE.search(text):
        raise ExplanationValidationError("Explanation contains a URL")

    # 6. Provider refusal text.
    if _REFUSAL_RE.search(text):
        raise ExplanationValidationError(
            "Explanation contains provider refusal language"
        )

    # 7. Requirement-completion / graduation-guarantee language.
    if _COMPLETION_RE.search(text):
        raise ExplanationValidationError(
            "Explanation uses prohibited completion language "
            "(use 'addresses' or 'contributes toward' instead)"
        )

    # 8. Student identity — synthetic fixture anchors.
    if _STUDENT_ID_RE.search(text):
        raise ExplanationValidationError(
            "Explanation contains a student ID pattern"
        )
    if re.search(r"\bStudent\s*,\s*Demo\b", text, re.IGNORECASE):
        raise ExplanationValidationError(
            "Explanation contains student name from fixture"
        )

    # 9. Course code guard — only uppercase codes are detected.
    #    Lowercase mentions of scheduled courses are intentionally not detected
    #    (see test_explainer.py: test_lowercase_course_code_passes).
    valid_codes: set[str] = set()
    for section in explainer_input.sections:
        code = section.course_code.upper()
        valid_codes.add(code)               # "CPSC 031"
        valid_codes.add(code.replace(" ", ""))  # "CPSC031"

    for subj, num in _COURSE_CODE_RE.findall(text):
        detected = f"{subj} {num}"
        detected_nospace = f"{subj}{num}"
        if detected not in valid_codes and detected_nospace not in valid_codes:
            raise ExplanationValidationError(
                f"Explanation references unknown course code: {detected}"
            )

    return text
