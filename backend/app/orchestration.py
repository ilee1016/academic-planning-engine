"""Application orchestration layer: connects parsed inputs to ranked results.

This module coordinates the deterministic pipeline stages without performing
any domain logic itself.  All rules live in core/ and adapters/.

Public API:
    AmbiguousCourse
    LockedResolutionResult
    PlanningResult
    resolve_locked_sections_flexible
    plan_schedules
    group_ranked_by_category

Invariants:
    INV-01  core/ modules never import from orchestration/.
    INV-40  No circular imports: orchestration → core/, adapters/, models.
    INV-API-06  Routes contain no domain logic.
    INV-API-09  Locked-section ambiguity is never resolved arbitrarily.
    INV-API-10  Capped solver output is disclosed via PlanningResult fields.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from decimal import Decimal

from app.adapters.swarthmore.requirement_defs import get_requirement_definitions
from app.core.diagnostics import diagnose_no_schedules
from app.core.ranking import rank_schedules
from app.core.requirements import build_requirement_status
from app.core.solver import (
    InvalidLockedScheduleError,
    SelectionOption,
    expand_selection_options,
    filter_candidates,
    generate_schedules,
)
from app.models import (
    CompletedCourse,
    ConstraintDiagnostic,
    CourseSection,
    Preferences,
    RankedSchedule,
    RequirementStatus,
    StudentRecord,
)

# Number of schedules kept per category in the grouped output.
_MAX_PER_CATEGORY = 3
# Default cap on globally-returned top schedules.
_DEFAULT_TOP_N = 10

_WS_RE = re.compile(r"\s+")


def _normalize_code(code: str) -> str:
    return _WS_RE.sub(" ", code).strip().upper()


# ---------------------------------------------------------------------------
# Locked-section resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmbiguousCourse:
    """A preregistered course that has multiple catalog sections to choose from."""

    course_code: str
    choices: tuple[CourseSection, ...]


@dataclass(frozen=True)
class LockedResolutionResult:
    """Outcome of flexible locked-section resolution.

    If ambiguous_courses is non-empty, the caller must not proceed to planning;
    it should surface the choices to the student.
    """

    locked_sections: tuple[CourseSection, ...]
    ambiguous_courses: tuple[AmbiguousCourse, ...]


def resolve_locked_sections_flexible(
    preregistered: list[CompletedCourse],
    catalog_sections: list[CourseSection],
    locked_ref_nos: frozenset[str],
) -> LockedResolutionResult:
    """Resolve preregistered courses to catalog sections without guessing.

    For each preregistered course:
    - If a matching ref_no was explicitly supplied → use that section.
    - Otherwise find all parent catalog sections with the same course code:
        - Exactly one match → auto-resolve.
        - Zero matches → treated as auto-resolve with no section (course not offered
          this semester; caller may warn or error).
        - Multiple matches → add to ambiguous_courses; caller must surface choices.

    INV-API-09: Locked-section ambiguity is never resolved arbitrarily.
    """
    # Index catalog by ref_no and by normalized course_code.
    by_ref: dict[str, CourseSection] = {s.ref_no: s for s in catalog_sections if s.is_parent}
    by_code: dict[str, list[CourseSection]] = {}
    for s in catalog_sections:
        if s.is_parent:
            norm = _normalize_code(s.course_code)
            by_code.setdefault(norm, []).append(s)

    locked: list[CourseSection] = []
    ambiguous: list[AmbiguousCourse] = []

    for course in preregistered:
        target = _normalize_code(course.code)
        # Check whether the student supplied an explicit ref_no for this course.
        explicit = [
            by_ref[rno]
            for rno in locked_ref_nos
            if rno in by_ref and _normalize_code(by_ref[rno].course_code) == target
        ]
        if explicit:
            locked.extend(explicit)
            continue
        # Auto-resolution.
        matches = by_code.get(target, [])
        if len(matches) == 1:
            locked.append(matches[0])
        elif len(matches) > 1:
            ambiguous.append(AmbiguousCourse(
                course_code=course.code,
                choices=tuple(matches),
            ))
        # len == 0: course not offered; caller decides whether to error or skip.

    return LockedResolutionResult(
        locked_sections=tuple(locked),
        ambiguous_courses=tuple(ambiguous),
    )


# ---------------------------------------------------------------------------
# Planning result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningResult:
    """Complete output of one plan_schedules() call.

    ranked_schedules: all ranked schedules, best first.
    diagnostic:       populated only when ranked_schedules is empty.
    solver_cap_reached: True when generated_count == max_results,
                        meaning there may be additional feasible schedules.
    INV-API-10: solver_cap status is always disclosed.
    """

    ranked_schedules: tuple[RankedSchedule, ...]
    diagnostic: ConstraintDiagnostic | None
    candidate_count: int
    option_count: int
    generated_count: int
    max_results: int
    solver_cap_reached: bool


# ---------------------------------------------------------------------------
# Grouping helper
# ---------------------------------------------------------------------------


def group_ranked_by_category(
    ranked: tuple[RankedSchedule, ...],
    top_n: int = _DEFAULT_TOP_N,
    max_per_category: int = _MAX_PER_CATEGORY,
) -> tuple[list[RankedSchedule], dict[str, list[RankedSchedule]]]:
    """Split ranked schedules into a global top list and per-category groups.

    Returns:
        (top_schedules, categories)
        top_schedules: up to top_n from the globally sorted ranked list
        categories: up to max_per_category per category; preserves ranked order
    """
    top_schedules = list(ranked[:top_n])
    categories: dict[str, list[RankedSchedule]] = {}
    for rs in ranked:
        cat = rs.category
        bucket = categories.setdefault(cat, [])
        if len(bucket) < max_per_category:
            bucket.append(rs)
    return top_schedules, categories


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def plan_schedules(
    *,
    student: StudentRecord,
    catalog_sections: list[CourseSection],
    requirement_status: RequirementStatus,
    preferences: Preferences,
    locked_ref_nos: frozenset[str],
    max_results: int = 500,
    max_ranked: int | None = None,
) -> PlanningResult:
    """Run the full planning pipeline and return ranked results or a diagnostic.

    Steps:
    1. Resolve locked sections (caller must have verified no ambiguity).
    2. filter_candidates
    3. expand_selection_options
    4. generate_schedules
    5. rank_schedules  (if results exist)
    6. diagnose_no_schedules  (if no results)

    Diagnostics receive candidate_sections for time-constraint probes.
    Domain inputs are never mutated.
    """
    # Step 1 — locked section resolution.
    lock_result = resolve_locked_sections_flexible(
        student.preregistered_courses if preferences.lock_preregistered else [],
        catalog_sections,
        locked_ref_nos,
    )
    # Callers should check ambiguity before calling plan_schedules; we continue
    # with whatever locked sections were resolved.
    locked: list[CourseSection] = list(lock_result.locked_sections)
    locked_refs: frozenset[str] = frozenset(s.ref_no for s in locked)

    # Step 2 — candidate filtering.
    candidates = filter_candidates(
        catalog_sections, student, requirement_status, preferences
    )

    # Step 3 — option expansion.
    options: list[SelectionOption] = expand_selection_options(candidates, preferences)

    # Step 4 — schedule generation.
    try:
        solver_schedules = generate_schedules(
            options, locked, preferences, max_results=max_results
        )
    except InvalidLockedScheduleError:
        # Locked sections conflict; generate empty list so diagnostics run.
        solver_schedules = []

    generated_count = len(solver_schedules)
    cap_reached = generated_count >= max_results

    # Step 5/6 — rank or diagnose.
    if solver_schedules:
        ranked = rank_schedules(
            solver_schedules,
            requirement_status,
            preferences,
            locked_ref_nos=locked_refs,
            max_ranked=max_ranked,
        )
        return PlanningResult(
            ranked_schedules=tuple(ranked),
            diagnostic=None,
            candidate_count=len(candidates),
            option_count=len(options),
            generated_count=generated_count,
            max_results=max_results,
            solver_cap_reached=cap_reached,
        )
    else:
        diag = diagnose_no_schedules(
            options=options,
            locked_sections=locked,
            preferences=preferences,
            candidate_sections=candidates,
        )
        return PlanningResult(
            ranked_schedules=(),
            diagnostic=diag,
            candidate_count=len(candidates),
            option_count=len(options),
            generated_count=0,
            max_results=max_results,
            solver_cap_reached=False,
        )


# ---------------------------------------------------------------------------
# Requirement status builder (used by input upload handler)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementBuildResult:
    requirement_status: RequirementStatus
    unmatched_ids: list[str]
    warnings: list[str]


def build_requirement_status_for_student(
    student: StudentRecord,
) -> RequirementBuildResult:
    """Build RequirementStatus, capturing unmatched IDs and warnings.

    Adapts the adapter + core functions for use in the upload handler.
    """
    definitions = get_requirement_definitions(student)
    captured_warnings: list[str] = []
    with warnings.catch_warnings(record=True) as w_list:
        warnings.simplefilter("always")
        req_status = build_requirement_status(student, definitions)
    for w in w_list:
        captured_warnings.append(str(w.message))

    # Identify items that generated a UserWarning (pattern: "Requirement '{id}': no ... matched")
    import re as _re
    unmatched: list[str] = []
    for msg in captured_warnings:
        m = _re.search(r"Requirement '([^']+)'", msg)
        if m:
            unmatched.append(m.group(1))

    return RequirementBuildResult(
        requirement_status=req_status,
        unmatched_ids=unmatched,
        warnings=captured_warnings,
    )
