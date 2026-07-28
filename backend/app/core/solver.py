"""Deterministic schedule solver for academic course planning.

Pipeline position:
  filter_candidates → expand_selection_options → generate_schedules

Public API:
    SelectionOption               frozen dataclass: parent + linked sections
    filter_candidates             remove ineligible candidates; sort by priority
    expand_selection_options      produce SelectionOptions from parent candidates
    resolve_locked_sections       match preregistered course codes to catalog sections
    generate_schedules            deterministic backtracking search; returns Schedule objects

Errors:
    SolverError                   base class
    LinkedSectionError            ambiguous or unsupported linked-section configuration
    InvalidLockedScheduleError    locked sections violate a hard constraint
    LockedSectionResolutionError  preregistered course could not be uniquely resolved

Hard constraints enforced as filters (Stage 4 MVP):
    min_credits, max_credits, free_days, earliest_start, latest_end,
    excluded_courses, completed-course exclusion, placement-exemption exclusion,
    locked-preregistered exclusion, unsupported course type, missing meeting times,
    time conflicts, duplicate course codes.

Preference fields (free_days, earliest_start, latest_end) are treated as HARD
constraints in Stage 4 — the solver returns no schedules violating them.
The ranker may still award preference-related scores for schedules that satisfy them.

Known limitations (Stage 4):
    FY Seminar sections are excluded for ALL students.  The catalog year field
    (e.g. "202304") is not a graduation year and cannot reliably identify first-year
    students.  This is a documented limitation; the correct fix requires a semester-
    to-class-year mapping that does not yet exist in the domain model.

    Standalone credit-bearing orphan labs (e.g. PHYS 063/081/082/083) are absent
    from parse_catalog() output and are not recovered here.

    Child credits are never added to SelectionOption.credits even for unusual
    credit-bearing linked children.  Seminar2 credits are counted once via the
    Seminar1 parent.

    With max_results capped at 500 (default), generate_schedules returns the FIRST
    deterministic set discovered under priority ordering, NOT the complete feasible set.

    resolve_locked_sections resolves only the parent section for each preregistered
    course.  The caller is responsible for adding the student's specific lab/drill
    section to locked_sections when that information is available.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from app.models import (
    CompletedCourse,
    CourseSection,
    Preferences,
    RequirementStatus,
    Schedule,
    StudentRecord,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SolverError(ValueError):
    """Base class for solver-layer errors."""


class LinkedSectionError(SolverError):
    """Ambiguous or unsupported linked-section configuration during option expansion."""


class InvalidLockedScheduleError(SolverError):
    """Locked sections violate a hard constraint before the search begins."""


class LockedSectionResolutionError(SolverError):
    """A preregistered course code could not be uniquely matched to a catalog section."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UNSUPPORTED_TYPES: frozenset[str] = frozenset(
    {"Directed Rdg", "Research Project", "Thesis", "Phys Educ", "Performance"}
)
_ALTERNATIVE_CHILD_TYPES: frozenset[str] = frozenset({"Lab", "Drill", "Language Section"})
_MANDATORY_CHILD_TYPES: frozenset[str] = frozenset({"Seminar2", "Attachment"})

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# SelectionOption
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionOption:
    """One atomic schedule selection: a parent section plus any required linked children.

    Produced by expand_selection_options(); consumed by generate_schedules().
    Lives in core/solver.py because it is solver-internal search data, not a public
    domain model used by other pipeline stages.

    credits returns only parent.credits.  Labs and drills carry Decimal("0") credits
    in the Swarthmore catalog, so summing child credits would not change the total.
    For Seminar1/Seminar2 pairs, credits are counted once (via the Seminar1 parent).
    """

    parent: CourseSection
    linked_sections: tuple[CourseSection, ...]

    @property
    def all_sections(self) -> tuple[CourseSection, ...]:
        return (self.parent,) + self.linked_sections

    @property
    def credits(self) -> Decimal:
        return self.parent.credits


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_code(code: str) -> str:
    """Normalize a course code for case-insensitive, whitespace-insensitive comparison."""
    return _WS_RE.sub(" ", code).strip().upper()


def _is_permitted_same_time_pair(left: CourseSection, right: CourseSection) -> bool:
    """True iff this parent+child pair is permitted to share the same meeting time.

    Seminar1/Seminar2: documented in BASELINE.md Section 4 (double-graded seminars
    meet simultaneously; count credits once via the Seminar1 parent).

    Parent + Attachment: observed in the Fall 2026 catalog (e.g. LATN 033 with its
    Attachment child at the same MWF time slot).  The Attachment appears to be a
    parallel registration vehicle, not a separate meeting.  Treating the pair as a
    permitted same-time combination prevents a false LinkedSectionError.
    """
    types = {left.course_type, right.course_type}
    if types == {"Seminar1", "Seminar2"}:
        return True
    if "Attachment" in types:
        return True
    return False


def _sections_conflict(
    left: CourseSection,
    right: CourseSection,
    *,
    allow_double_graded_pair: bool = False,
) -> bool:
    """True iff left and right have overlapping meeting times on a shared day.

    allow_double_graded_pair: when True, known permitted same-time pairs
    (Seminar1/Seminar2, parent+Attachment) are never treated as conflicting.
    See _is_permitted_same_time_pair for the full list.
    """
    if allow_double_graded_pair and _is_permitted_same_time_pair(left, right):
        return False
    return left.conflicts_with(right)


def _violates_time_window(
    section: CourseSection,
    earliest_start: time | None,
    latest_end: time | None,
) -> bool:
    """True iff any meeting of section falls outside the configured time window."""
    for mt in section.meeting_times:
        if earliest_start is not None and mt.start < earliest_start:
            return True
        if latest_end is not None and mt.end > latest_end:
            return True
    return False


def _violates_free_days(section: CourseSection, free_days: list[str]) -> bool:
    """True iff any meeting of section occurs on a forbidden day."""
    if not free_days:
        return False
    forbidden: frozenset[str] = frozenset(free_days)
    return any(bool(set(mt.days) & forbidden) for mt in section.meeting_times)


def _child_violates_preferences(
    child: CourseSection, preferences: Preferences | None
) -> bool:
    """True iff a linked child section violates the configured time window or free days."""
    if preferences is None:
        return False
    return _violates_time_window(
        child, preferences.earliest_start, preferences.latest_end
    ) or _violates_free_days(child, preferences.free_days)


def _validate_option(opt: SelectionOption) -> None:
    """Raise LinkedSectionError if the option contains structural or conflict problems.

    Allows Seminar1/Seminar2 same-time overlap (same course_code).
    All other internal conflicts are errors.
    """
    if not opt.parent.is_parent:
        raise LinkedSectionError(
            f"ref_no={opt.parent.ref_no!r}: section is not a parent type "
            f"(course_type={opt.parent.course_type!r})"
        )

    seen_refs: set[str] = set()
    for s in opt.all_sections:
        if s.ref_no in seen_refs:
            raise LinkedSectionError(
                f"Duplicate ref_no {s.ref_no!r} within SelectionOption for "
                f"{opt.parent.course_code}"
            )
        seen_refs.add(s.ref_no)

    for child in opt.linked_sections:
        if child.linked_sections:
            raise LinkedSectionError(
                f"Child section ref_no={child.ref_no!r} of {opt.parent.course_code} "
                f"has non-empty linked_sections (violates INV-10)"
            )

    all_secs = list(opt.all_sections)
    for i in range(len(all_secs)):
        for j in range(i + 1, len(all_secs)):
            s1, s2 = all_secs[i], all_secs[j]
            if _sections_conflict(s1, s2, allow_double_graded_pair=True):
                raise LinkedSectionError(
                    f"Internal time conflict in SelectionOption for "
                    f"{opt.parent.course_code}: ref_no {s1.ref_no!r} vs {s2.ref_no!r}. "
                    "Only Seminar1/Seminar2 same-time pairs are permitted."
                )


def _expand_one_parent(
    parent: CourseSection,
    preferences: Preferences | None,
) -> list[SelectionOption]:
    """Build all valid SelectionOptions for one parent section.

    Raises LinkedSectionError for unsupported or ambiguous child configurations.
    Returns [] if the parent has linked alternatives and all are filtered by preferences.
    """
    children = parent.linked_sections
    if not children:
        opt = SelectionOption(parent=parent, linked_sections=())
        _validate_option(opt)
        return [opt]

    alternative_children = [c for c in children if c.course_type in _ALTERNATIVE_CHILD_TYPES]
    mandatory_children = [c for c in children if c.course_type in _MANDATORY_CHILD_TYPES]
    unknown_children = [
        c for c in children
        if c.course_type not in _ALTERNATIVE_CHILD_TYPES
        and c.course_type not in _MANDATORY_CHILD_TYPES
    ]

    if unknown_children:
        types = {c.course_type for c in unknown_children}
        raise LinkedSectionError(
            f"{parent.course_code}: unsupported child section types {sorted(types)!r}. "
            "Cannot build SelectionOptions safely."
        )

    # Multiple Attachments are ambiguous; each Seminar2 is a separate mandatory option.
    attachments = [c for c in mandatory_children if c.course_type == "Attachment"]
    if len(attachments) > 1:
        raise LinkedSectionError(
            f"{parent.course_code}: {len(attachments)} Attachment children found. "
            "Ambiguous — cannot determine which are required. "
            "Document this as a known limitation or split into separate options manually."
        )

    if alternative_children:
        valid_alts = [
            c for c in alternative_children
            if not _child_violates_preferences(c, preferences)
        ]
        if not valid_alts:
            # All alternative child options filtered → this parent produces no options.
            return []
        options: list[SelectionOption] = []
        for alt in valid_alts:
            linked = (alt, *mandatory_children)
            opt = SelectionOption(parent=parent, linked_sections=linked)
            _validate_option(opt)
            options.append(opt)
        return options
    else:
        # Only mandatory children; no alternatives to choose from.
        opt = SelectionOption(parent=parent, linked_sections=tuple(mandatory_children))
        _validate_option(opt)
        return [opt]


def _any_section_conflicts(
    new_sections: tuple[CourseSection, ...],
    existing_sections: list[CourseSection],
) -> bool:
    """True iff any new section conflicts with any existing section."""
    for ns in new_sections:
        for es in existing_sections:
            if _sections_conflict(ns, es):
                return True
    return False


def _schedule_key(
    selected_options: list[SelectionOption],
    locked_sections: list[CourseSection],
) -> frozenset[str]:
    """Canonical schedule identity: frozenset of all section ref_nos."""
    refs: set[str] = set()
    for opt in selected_options:
        for s in opt.all_sections:
            refs.add(s.ref_no)
    for s in locked_sections:
        refs.add(s.ref_no)
    return frozenset(refs)


def _make_schedule(
    selected_options: list[SelectionOption],
    locked_sections: list[CourseSection],
    total_credits: Decimal,
) -> Schedule:
    """Assemble a Schedule from solver search state. Does not mutate inputs."""
    parent_secs: list[CourseSection] = []
    lab_secs: list[CourseSection] = []

    for s in locked_sections:
        if s.is_parent:
            parent_secs.append(s)
        else:
            lab_secs.append(s)

    for opt in selected_options:
        parent_secs.append(opt.parent)
        lab_secs.extend(opt.linked_sections)

    return Schedule(
        parent_sections=parent_secs,
        lab_sections=lab_secs,
        total_credits=total_credits,
    )


def _validate_locked_sections(
    locked_sections: list[CourseSection],
    preferences: Preferences,
) -> None:
    """Raise InvalidLockedScheduleError if locked sections violate any hard constraint.

    Checks: unique ref_nos, unique course codes (parents only), no pairwise time
    conflicts (same-course-code pairs allowed), time-window and free-day compliance.
    Credit-total validation is performed in generate_schedules after this call.
    """
    seen_refs: set[str] = set()
    for s in locked_sections:
        if s.ref_no in seen_refs:
            raise InvalidLockedScheduleError(
                f"Duplicate ref_no in locked sections: {s.ref_no!r}"
            )
        seen_refs.add(s.ref_no)

    seen_codes: set[str] = set()
    for s in locked_sections:
        if s.is_parent:
            if s.course_code in seen_codes:
                raise InvalidLockedScheduleError(
                    f"Duplicate course code in locked sections: {s.course_code!r}"
                )
            seen_codes.add(s.course_code)

    for i in range(len(locked_sections)):
        for j in range(i + 1, len(locked_sections)):
            if _sections_conflict(
                locked_sections[i], locked_sections[j], allow_double_graded_pair=True
            ):
                raise InvalidLockedScheduleError(
                    f"Time conflict between locked sections "
                    f"{locked_sections[i].ref_no!r} ({locked_sections[i].course_code}) "
                    f"and {locked_sections[j].ref_no!r} ({locked_sections[j].course_code})"
                )

    for s in locked_sections:
        if _violates_time_window(s, preferences.earliest_start, preferences.latest_end):
            raise InvalidLockedScheduleError(
                f"Locked section {s.ref_no!r} ({s.course_code}) violates "
                f"the configured time window"
            )
        if _violates_free_days(s, preferences.free_days):
            raise InvalidLockedScheduleError(
                f"Locked section {s.ref_no!r} ({s.course_code}) meets on "
                f"a configured free day"
            )


# ---------------------------------------------------------------------------
# Public — candidate filtering
# ---------------------------------------------------------------------------


def filter_candidates(
    sections: list[CourseSection],
    student: StudentRecord,
    requirement_status: RequirementStatus,
    preferences: Preferences,
) -> list[CourseSection]:
    """Return eligible parent sections sorted in deterministic priority order.

    Applies hard exclusion rules in sequence, then sorts by:
      1. Unsatisfied schedulable requirement items matched (descending)
      2. Preferred-subject position (earlier preference first; absent subjects last)
      3. Course code (ascending)
      4. Section ID (ascending)
      5. Ref_no (ascending)

    Hard exclusion rules applied in order:
      1. Non-parent sections (defensive guard)
      2. Unsupported course types (Directed Rdg, Research Project, Thesis, Phys Educ, Performance)
      3. No meeting times
      4. FY Seminar (excluded for all students — see module docstring limitation)
      5. Completed passing courses, with retake exception for unsatisfied requirements
      6. Placement-exempted courses
      7. Preregistered courses when preferences.lock_preregistered is True
      8. Time-window violations (parent's own meeting times)
      9. Free-day violations (parent's own meeting times)
      10. Explicit course exclusions (preferences.excluded_courses)

    Does not mutate sections, student, requirement_status, or preferences.
    """
    # Pre-compute exclusion sets.
    completed_passing: set[str] = {
        c.code for c in student.completed_courses if c.is_passing
    }
    retake_eligible: set[str] = {
        code
        for item in requirement_status.items
        if not item.satisfied and not item.auto_registered
        for code in item.satisfying_courses
    }
    exempted_normalized: set[str] = {_normalize_code(c) for c in student.exempted_courses}
    preregistered_normalized: set[str] = {
        _normalize_code(c.code) for c in student.preregistered_courses
    }
    excluded_normalized: set[str] = {
        _normalize_code(c) for c in preferences.excluded_courses
    }

    result: list[CourseSection] = []
    for s in sections:
        # Rule 1: only parent sections
        if not s.is_parent:
            continue

        # Rule 2: unsupported types
        if s.course_type in _UNSUPPORTED_TYPES:
            continue

        # Rule 3: must have meeting times
        if not s.meeting_times:
            continue

        # Rule 4: FY Seminar excluded for all students (Stage 4 limitation)
        if s.course_type == "FY Seminar":
            continue

        # Rule 5: completed passing — exclude unless retake-eligible
        if s.course_code in completed_passing and s.course_code not in retake_eligible:
            continue

        # Rule 6: placement exemptions
        if _normalize_code(s.course_code) in exempted_normalized:
            continue

        # Rule 7: preregistered when locked
        if (
            preferences.lock_preregistered
            and _normalize_code(s.course_code) in preregistered_normalized
        ):
            continue

        # Rule 8: time window (parent's own meetings only; child meetings checked in expand)
        if _violates_time_window(s, preferences.earliest_start, preferences.latest_end):
            continue

        # Rule 9: free days (parent's own meetings only)
        if _violates_free_days(s, preferences.free_days):
            continue

        # Rule 10: explicit course exclusions
        if _normalize_code(s.course_code) in excluded_normalized:
            continue

        result.append(s)

    preferred_subjects = preferences.preferred_subjects

    def _sort_key(section: CourseSection) -> tuple[int, int, str, str, str]:
        req_count = len(requirement_status.items_satisfied_by(section))
        try:
            subj_pos = preferred_subjects.index(section.subject)
        except ValueError:
            subj_pos = len(preferred_subjects)
        return (-req_count, subj_pos, section.course_code, section.section_id, section.ref_no)

    result.sort(key=_sort_key)
    return result


# ---------------------------------------------------------------------------
# Public — option expansion
# ---------------------------------------------------------------------------


def expand_selection_options(
    candidates: list[CourseSection],
    preferences: Preferences | None = None,
) -> list[SelectionOption]:
    """Produce SelectionOptions from a filtered candidate list.

    For each parent candidate, generates one SelectionOption per valid linked-child
    combination.  With preferences supplied, child meeting times are checked:
      - A child violating the time window or free days removes only that option.
      - If all alternative child options are removed, the parent produces no options.

    Linked-child semantics:
      Lab / Drill / Language Section  →  alternative (exactly one required)
      Seminar2 / single Attachment    →  mandatory (always included)
      Multiple Attachments            →  raises LinkedSectionError (ambiguous)
      Unrecognized child types        →  raises LinkedSectionError

    Seminar1/Seminar2 same-time pairs are allowed; all other internal option
    conflicts raise LinkedSectionError.

    Does not mutate candidates or any CourseSection.linked_sections list.
    """
    options: list[SelectionOption] = []
    for parent in candidates:
        parent_opts = _expand_one_parent(parent, preferences)
        options.extend(parent_opts)
    return options


# ---------------------------------------------------------------------------
# Public — locked section resolution
# ---------------------------------------------------------------------------


def resolve_locked_sections(
    preregistered: list[CompletedCourse],
    catalog_sections: list[CourseSection],
) -> list[CourseSection]:
    """Match each preregistered course to exactly one parent catalog section.

    Matching is by normalized course code (case-insensitive, whitespace-collapsed).
    Returns the matched parent sections in the same order as preregistered.

    Raises LockedSectionResolutionError when:
      - Zero catalog parent sections match a preregistered code (course not offered).
      - Multiple catalog parent sections match (multiple section offerings exist).
        When multiple matches exist, the frontend should prompt the student to select
        their exact registered section; this function cannot choose safely.

    Note: Only parent sections are resolved.  The student's specific lab/drill
    registration is not available in CompletedCourse.code.  The caller must add
    linked child sections separately if needed.

    Does not mutate preregistered or catalog_sections.
    """
    result: list[CourseSection] = []
    for course in preregistered:
        target = _normalize_code(course.code)
        matches = [
            s for s in catalog_sections
            if s.is_parent and _normalize_code(s.course_code) == target
        ]
        if len(matches) == 0:
            raise LockedSectionResolutionError(
                f"No catalog section found for preregistered course {course.code!r}. "
                "The course may not be offered this semester, or the course code "
                "format differs between the audit and catalog."
            )
        if len(matches) > 1:
            ref_nos = [s.ref_no for s in matches]
            raise LockedSectionResolutionError(
                f"Preregistered course {course.code!r} matches multiple catalog "
                f"sections: ref_nos {ref_nos!r}. "
                "The frontend must prompt the student to select their exact section."
            )
        result.append(matches[0])
    return result


# ---------------------------------------------------------------------------
# Public — schedule generation
# ---------------------------------------------------------------------------


def generate_schedules(
    options: list[SelectionOption],
    locked_sections: list[CourseSection],
    preferences: Preferences,
    max_results: int = 500,
) -> list[Schedule]:
    """Generate up to max_results valid schedules in deterministic priority order.

    Accepts pre-expanded SelectionOption objects (from expand_selection_options) and
    exact locked CourseSection objects (from resolve_locked_sections or caller-supplied).

    Hard constraints enforced during search:
      - No time conflicts between any two selected sections or locked sections.
      - No duplicate course codes (parent identity by course_code).
      - total_credits within [min_credits, max_credits].

    Boundary case — locked credits equal max_credits:
      Returns exactly one Schedule containing the locked sections only, without
      running backtracking.  The ranker or orchestration layer assigns the
      "current_registration" category; this function returns a plain Schedule.

    Returns [] if no combination reaches min_credits.

    Raises:
        ValueError: if max_results < 1.
        InvalidLockedScheduleError: if locked sections violate any hard constraint.

    Does not mutate options, locked_sections, preferences, or any CourseSection.
    """
    if max_results < 1:
        raise ValueError(f"max_results must be >= 1, got {max_results!r}")

    _validate_locked_sections(locked_sections, preferences)

    locked_credits: Decimal = sum(
        (s.credits for s in locked_sections if s.is_parent), Decimal("0")
    )

    if locked_credits > preferences.max_credits:
        raise InvalidLockedScheduleError(
            f"Locked credits ({locked_credits}) exceed max_credits "
            f"({preferences.max_credits})"
        )

    if locked_credits == preferences.max_credits:
        locked_parents = [s for s in locked_sections if s.is_parent]
        locked_labs = [s for s in locked_sections if not s.is_parent]
        return [
            Schedule(
                parent_sections=locked_parents,
                lab_sections=locked_labs,
                total_credits=locked_credits,
            )
        ]

    results: list[Schedule] = []
    seen_keys: set[frozenset[str]] = set()

    locked_codes: set[str] = {s.course_code for s in locked_sections if s.is_parent}

    # Mutable search state — mutated in-place and restored on backtrack.
    selected_options: list[SelectionOption] = []
    selected_sections: list[CourseSection] = list(locked_sections)
    selected_codes: set[str] = set(locked_codes)

    def _backtrack(start_idx: int, current_credits: Decimal) -> None:
        total_credits = locked_credits + current_credits

        if total_credits >= preferences.min_credits:
            key = _schedule_key(selected_options, locked_sections)
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(
                    _make_schedule(selected_options, locked_sections, total_credits)
                )

        if len(results) >= max_results:
            return

        for i in range(start_idx, len(options)):
            opt = options[i]

            if opt.parent.course_code in selected_codes:
                continue

            new_total = total_credits + opt.credits
            if new_total > preferences.max_credits:
                continue

            new_sections = opt.all_sections
            if _any_section_conflicts(new_sections, selected_sections):
                continue

            # Choose
            n = len(new_sections)
            selected_options.append(opt)
            selected_sections.extend(new_sections)
            selected_codes.add(opt.parent.course_code)

            _backtrack(i + 1, current_credits + opt.credits)

            # Unchoose
            del selected_sections[-n:]
            selected_options.pop()
            selected_codes.discard(opt.parent.course_code)

            if len(results) >= max_results:
                return

    _backtrack(0, Decimal("0"))
    return results
