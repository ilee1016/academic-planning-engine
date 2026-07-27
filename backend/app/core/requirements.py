"""Deterministic requirement status builder.

Consumes a StudentRecord and a list of RequirementDefinition objects (produced by an
adapter) and returns a RequirementStatus. Degree Works block statuses are authoritative;
this module never infers satisfaction from transcript courses.

Public API:
    build_requirement_status(student, definitions) -> RequirementStatus
    RequirementEvaluationError
"""
from __future__ import annotations

import re
import warnings
from dataclasses import replace
from decimal import Decimal

from app.models import (
    RequirementDefinition,
    RequirementItem,
    RequirementStatus,
    StudentRecord,
)


class RequirementEvaluationError(ValueError):
    """Raised when block matching is ambiguous (multiple blocks match one definition)."""


# ---------------------------------------------------------------------------
# Block-name normalization
# ---------------------------------------------------------------------------

_DASH_RE = re.compile(r"[–—−‒]")  # en-dash, em-dash, minus sign, figure dash
_WS_RE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    """Lowercase, collapse whitespace, normalize dash variants, strip ends."""
    name = _DASH_RE.sub("-", name)
    name = _WS_RE.sub(" ", name)
    return name.lower().strip()


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------


def build_requirement_status(
    student: StudentRecord,
    definitions: list[RequirementDefinition],
) -> RequirementStatus:
    """Build RequirementStatus from a student record and adapter-supplied definitions.

    For each definition, block_patterns are matched case-insensitively as substrings
    against the student's parsed Degree Works block names. Degree Works status is
    authoritative; transcript courses are never used to infer satisfaction.

    Raises:
        RequirementEvaluationError: if a definition's patterns match more than one block.
        ValueError: if any two definitions share the same requirement ID.

    Emits a UserWarning (via warnings.warn) for each definition whose patterns match
    no block in the student's audit. The item is left satisfied=False in that case.

    Does not mutate student, definitions, or nested lists within them.
    """
    _validate_unique_ids(definitions)

    normalized_blocks = [
        (_normalize(block.name), block.status)
        for block in student.requirement_blocks
    ]

    items: list[RequirementItem] = []
    for defn in definitions:
        normalized_patterns = [_normalize(p) for p in defn.block_patterns]

        # Find all distinct blocks matched by any pattern for this definition.
        matched: list[tuple[str, str]] = []  # (normalized_block_name, status)
        for norm_block, block_status in normalized_blocks:
            if any(pat in norm_block for pat in normalized_patterns):
                if (norm_block, block_status) not in matched:
                    matched.append((norm_block, block_status))

        if len(matched) == 0:
            warnings.warn(
                f"Requirement '{defn.item.id}': no Degree Works block matched "
                f"patterns {defn.block_patterns!r}. Item left unsatisfied.",
                stacklevel=2,
            )
            satisfied = False
        elif len(matched) > 1:
            block_names = [b for b, _ in matched]
            raise RequirementEvaluationError(
                f"Requirement '{defn.item.id}': patterns {defn.block_patterns!r} "
                f"matched multiple blocks: {block_names!r}. "
                "Tighten the block_patterns to avoid ambiguity."
            )
        else:
            _, block_status = matched[0]
            satisfied = block_status == "COMPLETE"

        # Create a new item without mutating the input.
        updated = replace(
            defn.item,
            satisfied=satisfied,
            satisfying_courses=list(defn.item.satisfying_courses),
        )
        items.append(updated)

    credits_remaining = max(
        Decimal("0"),
        student.credits_required - student.credits_applied,
    )
    return RequirementStatus(items=items, credits_remaining=credits_remaining)


def _validate_unique_ids(definitions: list[RequirementDefinition]) -> None:
    seen: set[str] = set()
    for defn in definitions:
        req_id = defn.item.id
        if req_id in seen:
            raise ValueError(f"Duplicate requirement ID: {req_id!r}")
        seen.add(req_id)
