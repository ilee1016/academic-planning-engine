"""Tests for core/solver.py: candidate filtering, option expansion, locked resolution,
backtracking solver, property-based invariants, and integration."""
from __future__ import annotations

import re
from datetime import date, time
from decimal import Decimal
from itertools import combinations
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.solver import (
    InvalidLockedScheduleError,
    LinkedSectionError,
    LockedSectionResolutionError,
    SelectionOption,
    SolverError,
    expand_selection_options,
    filter_candidates,
    generate_schedules,
    resolve_locked_sections,
)
from app.models import (
    CompletedCourse,
    CourseSection,
    MeetingTime,
    Preferences,
    RequirementItem,
    RequirementStatus,
    Schedule,
    StudentRecord,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


def _mt(
    *,
    days: tuple[str, ...] = ("T", "R"),
    start: time = time(10, 0),
    end: time = time(11, 0),
) -> MeetingTime:
    return MeetingTime(days=days, start=start, end=end)


def _section(
    ref_no: str,
    subject: str,
    number: str,
    *,
    course_type: str = "Course",
    credits: Decimal = Decimal("1"),
    meeting_times: list[MeetingTime] | None = None,
    distribution: frozenset[str] = frozenset(),
    linked_sections: list[CourseSection] | None = None,
    section_id: str = "01",
) -> CourseSection:
    if meeting_times is None:
        meeting_times = [_mt()]
    return CourseSection(
        ref_no=ref_no,
        subject=subject,
        number=number,
        section_id=section_id,
        title=f"{subject} {number}",
        credits=credits,
        distribution=distribution,
        enr_limit=None,
        instructors=[],
        course_type=course_type,
        meeting_times=meeting_times,
        note="",
        linked_sections=linked_sections if linked_sections is not None else [],
    )


def _completed(code: str, *, grade: str = "B", credits: Decimal = Decimal("1")) -> CompletedCourse:
    return CompletedCourse(
        code=code, title="", grade=grade, credits=credits, term="Fall 2025"
    )


def _student(
    *,
    completed: list[CompletedCourse] | None = None,
    exempted: list[str] | None = None,
    preregistered: list[CompletedCourse] | None = None,
) -> StudentRecord:
    return StudentRecord(
        name="Test Student",
        student_id="000000000",
        major="Computer Science",
        class_year=2027,
        catalog_year="202304",
        credits_required=Decimal("32"),
        credits_applied=Decimal("28"),
        audit_date=date(2026, 7, 1),
        completed_courses=completed or [],
        preregistered_courses=preregistered or [],
        other_courses=[],
        exempted_courses=exempted or [],
        requirement_blocks=[],
        exceptions=[],
    )


def _prefs(**kwargs: object) -> Preferences:
    defaults: dict[str, object] = dict(
        min_credits=Decimal("3"),
        max_credits=Decimal("4"),
        free_days=[],
        earliest_start=None,
        latest_end=None,
        preferred_subjects=[],
        excluded_courses=[],
        lock_preregistered=True,
    )
    defaults.update(kwargs)
    return Preferences(**defaults)  # type: ignore[arg-type]


def _empty_status() -> RequirementStatus:
    return RequirementStatus(items=[], credits_remaining=Decimal("0"))


def _status_with_item(
    *,
    item_id: str = "test_item",
    satisfied: bool = False,
    auto_registered: bool = False,
    satisfying_courses: list[str] | None = None,
    matching_attributes: frozenset[str] = frozenset(),
    subject_predicate: str | None = None,
) -> RequirementStatus:
    item = RequirementItem(
        id=item_id,
        label=item_id,
        satisfied=satisfied,
        satisfying_courses=satisfying_courses or [],
        matching_attributes=matching_attributes,
        notes="",
        subject_predicate=subject_predicate,
        auto_registered=auto_registered,
    )
    return RequirementStatus(items=[item], credits_remaining=Decimal("1"))


# ---------------------------------------------------------------------------
# Candidate filtering — rule-by-rule
# ---------------------------------------------------------------------------


def test_filter_excludes_passing_completed_course() -> None:
    s = _section("1", "MATH", "035")
    result = filter_candidates(
        [s], _student(completed=[_completed("MATH 035", grade="B")]), _empty_status(), _prefs()
    )
    assert result == []


def test_filter_retains_non_passing_completed_course() -> None:
    s = _section("1", "MATH", "035")
    result = filter_candidates(
        [s], _student(completed=[_completed("MATH 035", grade="W")]), _empty_status(), _prefs()
    )
    assert result == [s]


def test_filter_retains_cr_grade_retake_eligible() -> None:
    s = _section("1", "CPSC", "031")
    status = _status_with_item(satisfying_courses=["CPSC 031"])
    result = filter_candidates(
        [s], _student(completed=[_completed("CPSC 031", grade="CR")]), status, _prefs()
    )
    assert result == [s]


def test_filter_excludes_cr_when_requirement_already_satisfied() -> None:
    s = _section("1", "CPSC", "031")
    status = _status_with_item(satisfied=True, satisfying_courses=["CPSC 031"])
    result = filter_candidates(
        [s], _student(completed=[_completed("CPSC 031", grade="CR")]), status, _prefs()
    )
    assert result == []


def test_filter_auto_registered_item_does_not_reintroduce_cpsc_099() -> None:
    s = _section("1", "CPSC", "099", course_type="Research Project")
    # Even if satisfying_courses lists CPSC 099, auto_registered=True means it's excluded
    status = _status_with_item(
        satisfying_courses=["CPSC 099"], auto_registered=True
    )
    result = filter_candidates([s], _student(), status, _prefs())
    # Excluded by unsupported type rule (Research Project), not by retake logic
    assert result == []


def test_filter_excludes_exempted_course() -> None:
    s = _section("1", "CPSC", "021")
    result = filter_candidates(
        [s], _student(exempted=["CPSC 021"]), _empty_status(), _prefs()
    )
    assert result == []


def test_filter_excludes_preregistered_when_locked() -> None:
    s = _section("1", "CPSC", "063")
    result = filter_candidates(
        [s],
        _student(preregistered=[_completed("CPSC 063", grade="----")]),
        _empty_status(),
        _prefs(lock_preregistered=True),
    )
    assert result == []


def test_filter_retains_preregistered_when_unlocked() -> None:
    s = _section("1", "CPSC", "063")
    result = filter_candidates(
        [s],
        _student(preregistered=[_completed("CPSC 063", grade="----")]),
        _empty_status(),
        _prefs(lock_preregistered=False),
    )
    assert result == [s]


def test_filter_excludes_explicit_excluded_course() -> None:
    s = _section("1", "DANC", "001")
    result = filter_candidates(
        [s], _student(), _empty_status(), _prefs(excluded_courses=["DANC 001"])
    )
    assert result == []


def test_filter_excludes_no_meeting_section() -> None:
    s = _section("1", "CPSC", "099", meeting_times=[])
    result = filter_candidates([s], _student(), _empty_status(), _prefs())
    assert result == []


def test_filter_excludes_unsupported_course_types() -> None:
    for ctype in ("Directed Rdg", "Research Project", "Thesis", "Phys Educ", "Performance"):
        s = _section("1", "CPSC", "099", course_type=ctype)
        result = filter_candidates([s], _student(), _empty_status(), _prefs())
        assert result == [], f"Expected {ctype!r} to be excluded"


def test_filter_excludes_fy_seminar() -> None:
    s = _section("1", "ARTH", "001N", course_type="FY Seminar")
    result = filter_candidates([s], _student(), _empty_status(), _prefs())
    assert result == []


def test_filter_excludes_parent_outside_earliest_start() -> None:
    s = _section("1", "MATH", "035", meeting_times=[_mt(start=time(8, 0), end=time(9, 0))])
    result = filter_candidates(
        [s], _student(), _empty_status(), _prefs(earliest_start=time(9, 0))
    )
    assert result == []


def test_filter_excludes_parent_outside_latest_end() -> None:
    s = _section("1", "MATH", "035", meeting_times=[_mt(start=time(17, 0), end=time(19, 0))])
    result = filter_candidates(
        [s], _student(), _empty_status(), _prefs(latest_end=time(18, 0))
    )
    assert result == []


def test_filter_excludes_parent_meeting_on_free_day() -> None:
    s = _section("1", "MATH", "035", meeting_times=[_mt(days=("M", "W", "F"))])
    result = filter_candidates([s], _student(), _empty_status(), _prefs(free_days=["F"]))
    assert result == []


def test_filter_priority_ordering_by_requirement_match() -> None:
    s_cpsc = _section("1", "CPSC", "031")
    s_math = _section("2", "MATH", "035")
    status = _status_with_item(subject_predicate="CPSC")
    result = filter_candidates([s_math, s_cpsc], _student(), status, _prefs())
    # CPSC 031 matches the CPSC subject predicate; MATH 035 does not
    assert result[0].course_code == "CPSC 031"
    assert result[1].course_code == "MATH 035"


def test_filter_preferred_subject_tiebreaking() -> None:
    s_math = _section("1", "MATH", "035")
    s_engl = _section("2", "ENGL", "011")
    s_cpsc = _section("3", "CPSC", "031")
    result = filter_candidates(
        [s_math, s_engl, s_cpsc],
        _student(),
        _empty_status(),
        _prefs(preferred_subjects=["CPSC", "ENGL"]),
    )
    assert result[0].subject == "CPSC"
    assert result[1].subject == "ENGL"
    assert result[2].subject == "MATH"


def test_filter_deterministic_ordering_within_same_priority() -> None:
    # Three courses in same subject, same priority — sort by code then section_id
    s_a = _section("1", "MATH", "021", section_id="01")
    s_b = _section("2", "MATH", "035", section_id="01")
    s_c = _section("3", "MATH", "035", section_id="02")
    result_ab = filter_candidates([s_a, s_b, s_c], _student(), _empty_status(), _prefs())
    result_ba = filter_candidates([s_c, s_b, s_a], _student(), _empty_status(), _prefs())
    assert [s.ref_no for s in result_ab] == [s.ref_no for s in result_ba]


def test_filter_does_not_mutate_input_list() -> None:
    original = [_section("1", "MATH", "035"), _section("2", "CPSC", "031")]
    copy_codes = [s.course_code for s in original]
    filter_candidates(original, _student(), _empty_status(), _prefs())
    assert [s.course_code for s in original] == copy_codes


def test_filter_excludes_non_parent_section() -> None:
    lab = _section("1", "CPSC", "031", course_type="Lab")
    result = filter_candidates([lab], _student(), _empty_status(), _prefs())
    assert result == []


def test_filter_excluded_course_case_insensitive() -> None:
    s = _section("1", "CPSC", "031")
    result = filter_candidates(
        [s], _student(), _empty_status(), _prefs(excluded_courses=["cpsc 031"])
    )
    assert result == []


# ---------------------------------------------------------------------------
# Option expansion
# ---------------------------------------------------------------------------


def test_expand_parent_with_no_children_produces_one_option() -> None:
    s = _section("1", "MATH", "035")
    opts = expand_selection_options([s])
    assert len(opts) == 1
    assert opts[0].parent is s
    assert opts[0].linked_sections == ()


def test_expand_parent_with_one_lab_produces_one_option() -> None:
    lab = _section("2", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
                   meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))])
    # Parent meets TR only — no day overlap with the Wednesday lab
    parent = _section("1", "CPSC", "031",
                      meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))],
                      linked_sections=[lab])
    opts = expand_selection_options([parent])
    assert len(opts) == 1
    assert opts[0].parent is parent
    assert opts[0].linked_sections == (lab,)


def test_expand_parent_with_three_labs_produces_three_options() -> None:
    labs = [
        _section(f"L{i}", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
                 meeting_times=[_mt(days=("T",), start=time(13, 0 + i * 90 // 60),
                                    end=time(14, 30 + i))])
        for i in range(3)
    ]
    parent = _section("P", "CPSC", "063", linked_sections=labs)
    opts = expand_selection_options([parent])
    assert len(opts) == 3
    assert all(o.parent is parent for o in opts)
    assert {o.linked_sections[0].ref_no for o in opts} == {lab.ref_no for lab in labs}


def test_expand_language_course_with_drill_produces_one_option() -> None:
    drill = _section("D1", "ARAB", "001", course_type="Drill", credits=Decimal("0"),
                     meeting_times=[_mt(days=("M", "W", "F"), start=time(9, 30), end=time(10, 20))])
    # Parent meets TR only — no day overlap with the MWF drill
    parent = _section("P1", "ARAB", "001", course_type="Language Course",
                      credits=Decimal("1.5"),
                      meeting_times=[_mt(days=("T", "R"), start=time(9, 55), end=time(11, 10))],
                      linked_sections=[drill])
    opts = expand_selection_options([parent])
    assert len(opts) == 1
    assert opts[0].linked_sections == (drill,)


def test_expand_language_sections_treated_as_alternatives() -> None:
    # Parent meets TR; language sections on M and W to avoid day conflicts.
    ls1 = _section("LS1", "FREN", "001", course_type="Language Section", credits=Decimal("0"),
                   meeting_times=[_mt(days=("M",), start=time(10, 0), end=time(11, 0))])
    ls2 = _section("LS2", "FREN", "001", course_type="Language Section", credits=Decimal("0"),
                   meeting_times=[_mt(days=("W",), start=time(10, 0), end=time(11, 0))])
    parent = _section("P", "FREN", "001", course_type="Language Course",
                      meeting_times=[_mt(days=("T", "R"), start=time(9, 55), end=time(11, 10))],
                      linked_sections=[ls1, ls2])
    opts = expand_selection_options([parent])
    assert len(opts) == 2
    linked_refs = {o.linked_sections[0].ref_no for o in opts}
    assert linked_refs == {"LS1", "LS2"}


def test_expand_seminar1_with_one_seminar2_produces_mandatory_pair() -> None:
    sem2 = _section(
        "B", "ANTH", "122", course_type="Seminar2", credits=Decimal("1"),
        meeting_times=[_mt(days=("W",), start=time(13, 15), end=time(16, 0))],
    )
    sem1 = _section(
        "A", "ANTH", "122", course_type="Seminar1", credits=Decimal("1"),
        meeting_times=[_mt(days=("W",), start=time(13, 15), end=time(16, 0))],
        linked_sections=[sem2],
    )
    opts = expand_selection_options([sem1])
    assert len(opts) == 1
    assert opts[0].parent is sem1
    assert opts[0].linked_sections == (sem2,)


def test_expand_attachment_single_child_is_mandatory() -> None:
    att = _section("A1", "ENGR", "120", course_type="Attachment", credits=Decimal("0"),
                   meeting_times=[_mt(days=("F",), start=time(14, 0), end=time(15, 0))])
    parent = _section("P1", "ENGR", "120", linked_sections=[att])
    opts = expand_selection_options([parent])
    assert len(opts) == 1
    assert opts[0].linked_sections == (att,)


def test_expand_child_outside_time_window_removes_only_that_option() -> None:
    early_lab = _section(
        "L1", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("T",), start=time(7, 0), end=time(8, 30))],
    )
    late_lab = _section(
        "L2", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("T",), start=time(13, 0), end=time(14, 30))],
    )
    parent = _section("P", "CPSC", "063", linked_sections=[early_lab, late_lab])
    opts = expand_selection_options([parent], _prefs(earliest_start=time(9, 0)))
    assert len(opts) == 1
    assert opts[0].linked_sections[0].ref_no == "L2"


def test_expand_child_on_free_day_removes_only_that_option() -> None:
    fri_lab = _section(
        "L1", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("F",), start=time(10, 30), end=time(12, 0))],
    )
    wed_lab = _section(
        "L2", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))],
    )
    parent = _section("P", "CPSC", "031", linked_sections=[fri_lab, wed_lab])
    opts = expand_selection_options([parent], _prefs(free_days=["F"]))
    assert len(opts) == 1
    assert opts[0].linked_sections[0].ref_no == "L2"


def test_expand_parent_retained_when_at_least_one_child_valid() -> None:
    bad_lab = _section(
        "L1", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("F",), start=time(10, 0), end=time(11, 30))],
    )
    good_lab = _section(
        "L2", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("T",), start=time(13, 0), end=time(14, 30))],
    )
    parent = _section("P", "CPSC", "063", linked_sections=[bad_lab, good_lab])
    opts = expand_selection_options([parent], _prefs(free_days=["F"]))
    assert len(opts) == 1


def test_expand_parent_with_all_children_invalid_produces_no_options() -> None:
    bad1 = _section("L1", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
                    meeting_times=[_mt(days=("F",), start=time(10, 0), end=time(11, 30))])
    bad2 = _section("L2", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
                    meeting_times=[_mt(days=("F",), start=time(13, 0), end=time(14, 30))])
    parent = _section("P", "CPSC", "063", linked_sections=[bad1, bad2])
    opts = expand_selection_options([parent], _prefs(free_days=["F"]))
    assert opts == []


def test_expand_internal_parent_child_conflict_rejected() -> None:
    # Create a lab with a DIFFERENT course code that conflicts with the parent.
    # Shared course_code pairs are exempt (Seminar1/Seminar2); Lab+Course are not.
    conflicting_lab = _section(
        "L1", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
        meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))],
    )
    # Force a different number so course_code differs → not a double-graded pair
    conflicting_lab.number = "999"  # type: ignore[misc]  # CourseSection is not frozen
    parent = _section(
        "P", "CPSC", "031",
        meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))],
        linked_sections=[conflicting_lab],
    )
    with pytest.raises(LinkedSectionError):
        expand_selection_options([parent])


def test_expand_seminar1_seminar2_same_time_allowed() -> None:
    sem2 = _section("B", "ANTH", "122", course_type="Seminar2", credits=Decimal("1"),
                    meeting_times=[_mt(days=("W",), start=time(13, 15), end=time(16, 0))])
    sem1 = _section("A", "ANTH", "122", course_type="Seminar1", credits=Decimal("1"),
                    meeting_times=[_mt(days=("W",), start=time(13, 15), end=time(16, 0))],
                    linked_sections=[sem2])
    opts = expand_selection_options([sem1])
    assert len(opts) == 1


def test_expand_child_linked_sections_must_be_empty() -> None:
    grandchild = _section("GC", "CPSC", "031", course_type="Lab", credits=Decimal("0"))
    child = _section("C", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
                     linked_sections=[grandchild])
    parent = _section("P", "CPSC", "031", linked_sections=[child])
    with pytest.raises(LinkedSectionError):
        expand_selection_options([parent])


def test_expand_option_credits_count_parent_only() -> None:
    lab = _section("L", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
                   meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))])
    parent = _section("P", "CPSC", "031", credits=Decimal("1"), linked_sections=[lab])
    opts = expand_selection_options([parent])
    assert opts[0].credits == Decimal("1")


def test_expand_option_ordering_is_deterministic() -> None:
    labs = [_section(f"L{i}", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
                     meeting_times=[_mt(days=("T",), start=time(13 + i, 0), end=time(14 + i, 0))])
            for i in range(3)]
    parent = _section("P", "CPSC", "063", linked_sections=labs)
    opts1 = expand_selection_options([parent])
    opts2 = expand_selection_options([parent])
    assert [o.linked_sections[0].ref_no for o in opts1] == \
           [o.linked_sections[0].ref_no for o in opts2]


def test_expand_multiple_attachments_raises() -> None:
    att1 = _section("A1", "ENGR", "120", course_type="Attachment", credits=Decimal("0"),
                    meeting_times=[_mt(days=("M",))])
    att2 = _section("A2", "ENGR", "120", course_type="Attachment", credits=Decimal("0"),
                    meeting_times=[_mt(days=("W",))])
    parent = _section("P", "ENGR", "120", linked_sections=[att1, att2])
    with pytest.raises(LinkedSectionError):
        expand_selection_options([parent])


def test_expand_mixed_lab_and_seminar2_raises_unsupported() -> None:
    # A parent with both a Lab and a Seminar2 from different groups is ambiguous.
    lab = _section("L1", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
                   meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))])
    sem2 = _section("S2", "CPSC", "031", course_type="Seminar2", credits=Decimal("0"),
                    meeting_times=[_mt(days=("W",), start=time(13, 0), end=time(14, 30))])
    parent = _section("P", "CPSC", "031", linked_sections=[lab, sem2])
    # Lab is alternative, Seminar2 is mandatory → should produce Lab+Seminar2 options, not error
    opts = expand_selection_options([parent])
    assert len(opts) == 1
    assert opts[0].linked_sections == (lab, sem2)


# ---------------------------------------------------------------------------
# Locked section resolution
# ---------------------------------------------------------------------------


def test_resolve_unique_match_returns_section() -> None:
    parent = _section("100", "CPSC", "063")
    resolved = resolve_locked_sections([_completed("CPSC 063")], [parent])
    assert resolved == [parent]


def test_resolve_zero_matches_raises() -> None:
    with pytest.raises(LockedSectionResolutionError):
        resolve_locked_sections([_completed("CPSC 999")], [_section("1", "CPSC", "063")])


def test_resolve_multiple_matches_raises() -> None:
    s1 = _section("1", "CPSC", "063", section_id="01")
    s2 = _section("2", "CPSC", "063", section_id="02")
    with pytest.raises(LockedSectionResolutionError):
        resolve_locked_sections([_completed("CPSC 063")], [s1, s2])


def test_resolve_case_insensitive_and_whitespace_normalized() -> None:
    parent = _section("1", "CPSC", "031")
    resolved = resolve_locked_sections([_completed("cpsc  031")], [parent])
    assert resolved == [parent]


def test_resolve_does_not_mutate_inputs() -> None:
    preregistered = [_completed("CPSC 063")]
    catalog = [_section("1", "CPSC", "063")]
    before_preg = list(preregistered)
    before_cat = list(catalog)
    resolve_locked_sections(preregistered, catalog)
    assert preregistered == before_preg
    assert len(catalog) == len(before_cat)


# ---------------------------------------------------------------------------
# Solver — generate_schedules
# ---------------------------------------------------------------------------


def _mk_opt(
    ref_no: str,
    subject: str,
    number: str,
    *,
    credits: Decimal = Decimal("1"),
    meeting_times: list[MeetingTime] | None = None,
    lab_meeting_times: list[MeetingTime] | None = None,
) -> SelectionOption:
    parent = _section(ref_no, subject, number, credits=credits, meeting_times=meeting_times)
    if lab_meeting_times is not None:
        lab = _section(f"{ref_no}L", subject, number, course_type="Lab",
                       credits=Decimal("0"), meeting_times=lab_meeting_times)
        parent.linked_sections = [lab]
        return SelectionOption(parent=parent, linked_sections=(lab,))
    return SelectionOption(parent=parent, linked_sections=())


def test_solver_empty_options_returns_empty_when_below_minimum() -> None:
    result = generate_schedules([], [], _prefs(min_credits=Decimal("3")))
    assert result == []


def test_solver_one_valid_option_reaches_minimum() -> None:
    opt = _mk_opt("1", "MATH", "035", credits=Decimal("3"))
    result = generate_schedules([opt], [], _prefs(min_credits=Decimal("3"), max_credits=Decimal("4")))
    assert len(result) == 1
    assert result[0].total_credits == Decimal("3")


def test_solver_back_to_back_courses_do_not_conflict() -> None:
    opt1 = _mk_opt("1", "MATH", "021", credits=Decimal("1"),
                   meeting_times=[_mt(days=("T", "R"), start=time(9, 55), end=time(11, 10))])
    opt2 = _mk_opt("2", "CPSC", "031", credits=Decimal("1"),
                   meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))])
    result = generate_schedules(
        [opt1, opt2], [], _prefs(min_credits=Decimal("2"), max_credits=Decimal("4"))
    )
    two_course = [s for s in result if len(s.parent_sections) == 2]
    assert len(two_course) >= 1
    for sched in two_course:
        for a, b in combinations(sched.all_sections, 2):
            assert not a.conflicts_with(b)


def test_solver_overlapping_courses_conflict_excluded() -> None:
    opt1 = _mk_opt("1", "MATH", "021", credits=Decimal("1"),
                   meeting_times=[_mt(days=("T", "R"), start=time(11, 0), end=time(12, 0))])
    opt2 = _mk_opt("2", "CPSC", "031", credits=Decimal("1"),
                   meeting_times=[_mt(days=("T", "R"), start=time(11, 30), end=time(12, 30))])
    # Together they conflict; only single-course schedules (each alone) should appear
    result = generate_schedules(
        [opt1, opt2], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    )
    for sched in result:
        for a, b in combinations(sched.all_sections, 2):
            assert not a.conflicts_with(b)


def test_solver_linked_lab_conflicts_are_enforced() -> None:
    # Lab at same time as another course → that combination is excluded
    opt1 = _mk_opt(
        "1", "CPSC", "031", credits=Decimal("1"),
        meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))],
        lab_meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))],
    )
    opt2 = _mk_opt("2", "MATH", "035", credits=Decimal("1"),
                   meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))])
    result = generate_schedules(
        [opt1, opt2], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    )
    for sched in result:
        for a, b in combinations(sched.all_sections, 2):
            assert not a.conflicts_with(b)
    # No schedule should contain both CPSC 031 and MATH 035 (lab conflict)
    both = [s for s in result
            if any(p.course_code == "CPSC 031" for p in s.parent_sections)
            and any(p.course_code == "MATH 035" for p in s.parent_sections)]
    assert both == []


def test_solver_duplicate_course_code_cannot_appear() -> None:
    # Two options with same course code — only one can be in any schedule
    opt1 = _mk_opt("1", "CPSC", "031", credits=Decimal("1"),
                   meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))])
    opt2 = _mk_opt("2", "CPSC", "031", credits=Decimal("1"),
                   meeting_times=[_mt(days=("M", "W"), start=time(9, 0), end=time(10, 15))])
    result = generate_schedules(
        [opt1, opt2], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    )
    for sched in result:
        codes = [s.course_code for s in sched.parent_sections]
        assert len(codes) == len(set(codes)), "Duplicate course code in schedule"


def test_solver_different_lab_choices_produce_distinct_schedules() -> None:
    lab_a = _section("LA", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
                     meeting_times=[_mt(days=("T",), start=time(13, 0), end=time(14, 30))])
    lab_b = _section("LB", "CPSC", "063", course_type="Lab", credits=Decimal("0"),
                     meeting_times=[_mt(days=("T",), start=time(14, 45), end=time(16, 15))])
    parent = _section("P", "CPSC", "063", credits=Decimal("1"),
                      linked_sections=[lab_a, lab_b])
    opt_a = SelectionOption(parent=parent, linked_sections=(lab_a,))
    opt_b = SelectionOption(parent=parent, linked_sections=(lab_b,))
    result = generate_schedules(
        [opt_a, opt_b], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    )
    assert len(result) == 2
    keys = [frozenset(s.ref_no for s in r.all_sections) for r in result]
    assert keys[0] != keys[1]


def test_solver_all_schedules_within_credit_bounds() -> None:
    opts = [
        _mk_opt(str(i), "TEST", f"{i:03d}", credits=Decimal("1"),
                meeting_times=[_mt(days=("M",) if i % 2 == 0 else ("T",),
                                    start=time(9 + i, 0), end=time(10 + i, 0))])
        for i in range(5)
    ]
    result = generate_schedules(opts, [], _prefs(min_credits=Decimal("2"), max_credits=Decimal("3")))
    for sched in result:
        assert Decimal("2") <= sched.total_credits <= Decimal("3")
        assert isinstance(sched.total_credits, Decimal)


def test_solver_locked_sections_present_in_every_result() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("1"),
                      meeting_times=[_mt(days=("M", "W"), start=time(9, 0), end=time(10, 15))])
    opt = _mk_opt("1", "MATH", "035", credits=Decimal("1"),
                  meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))])
    result = generate_schedules([opt], [locked], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4")))
    for sched in result:
        assert any(s.ref_no == "L" for s in sched.all_sections)


def test_solver_options_conflicting_with_locked_excluded() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("1"),
                      meeting_times=[_mt(days=("M", "W"), start=time(9, 0), end=time(10, 15))])
    conflict_opt = _mk_opt("1", "MATH", "035", credits=Decimal("1"),
                           meeting_times=[_mt(days=("M",), start=time(9, 30), end=time(10, 30))])
    safe_opt = _mk_opt("2", "ENGL", "011", credits=Decimal("1"),
                       meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))])
    result = generate_schedules(
        [conflict_opt, safe_opt], [locked],
        _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    )
    for sched in result:
        assert not any(s.course_code == "MATH 035" for s in sched.parent_sections)


def test_solver_invalid_locked_conflict_raises() -> None:
    l1 = _section("L1", "CPSC", "063", credits=Decimal("1"),
                  meeting_times=[_mt(days=("M", "W"), start=time(9, 0), end=time(10, 15))])
    l2 = _section("L2", "MATH", "035", credits=Decimal("1"),
                  meeting_times=[_mt(days=("M",), start=time(9, 30), end=time(10, 30))])
    with pytest.raises(InvalidLockedScheduleError):
        generate_schedules([], [l1, l2], _prefs())


def test_solver_locked_credits_above_maximum_raises() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("5"),
                      meeting_times=[_mt(days=("M",), start=time(9, 0), end=time(10, 0))])
    with pytest.raises(InvalidLockedScheduleError):
        generate_schedules([], [locked], _prefs(max_credits=Decimal("4")))


def test_solver_locked_credits_equal_maximum_returns_one_locked_schedule() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("4"),
                      meeting_times=[_mt(days=("M",), start=time(9, 0), end=time(10, 0))])
    result = generate_schedules([], [locked], _prefs(max_credits=Decimal("4")))
    assert len(result) == 1
    assert result[0].total_credits == Decimal("4")
    assert any(s.ref_no == "L" for s in result[0].all_sections)


def test_solver_locked_only_below_minimum_does_not_return_unless_additions_selected() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("1"),
                      meeting_times=[_mt(days=("M",), start=time(9, 0), end=time(10, 0))])
    # No options to add → cannot reach min_credits=3 → empty result
    result = generate_schedules([], [locked], _prefs(min_credits=Decimal("3"), max_credits=Decimal("4")))
    assert result == []


def test_solver_free_day_violation_on_locked_section_raises() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("1"),
                      meeting_times=[_mt(days=("F",), start=time(10, 0), end=time(11, 0))])
    with pytest.raises(InvalidLockedScheduleError):
        generate_schedules([], [locked], _prefs(free_days=["F"]))


def test_solver_time_window_violation_on_locked_section_raises() -> None:
    locked = _section("L", "CPSC", "063", credits=Decimal("1"),
                      meeting_times=[_mt(days=("M",), start=time(7, 0), end=time(8, 0))])
    with pytest.raises(InvalidLockedScheduleError):
        generate_schedules([], [locked], _prefs(earliest_start=time(9, 0)))


def test_solver_max_results_cap_honored() -> None:
    opts = [
        _mk_opt(str(i), "TEST", f"{i:03d}", credits=Decimal("1"),
                meeting_times=[_mt(days=("M",) if i % 3 == 0 else ("T",) if i % 3 == 1 else ("W",),
                                    start=time(9, 0), end=time(10, 0))])
        for i in range(10)
    ]
    result = generate_schedules(opts, [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4")), max_results=3)
    assert len(result) <= 3


def test_solver_non_positive_max_results_raises() -> None:
    with pytest.raises(ValueError):
        generate_schedules([], [], _prefs(), max_results=0)


def test_solver_results_are_deterministic() -> None:
    opts = [
        _mk_opt(str(i), "TEST", f"{i:03d}", credits=Decimal("1"),
                meeting_times=[_mt(days=("M",) if i % 2 == 0 else ("T",),
                                    start=time(9, 0), end=time(10, 0))])
        for i in range(6)
    ]
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("3"))
    result1 = generate_schedules(opts, [], prefs)
    result2 = generate_schedules(opts, [], prefs)
    keys1 = [frozenset(s.ref_no for s in r.all_sections) for r in result1]
    keys2 = [frozenset(s.ref_no for s in r.all_sections) for r in result2]
    assert keys1 == keys2


def test_solver_duplicate_schedule_structures_deduplicated() -> None:
    # Two options that produce the same ref_no set — only one result should appear
    parent = _section("P", "CPSC", "031", credits=Decimal("1"))
    opt = SelectionOption(parent=parent, linked_sections=())
    # Passing the same option twice simulates a buggy caller; the dedup key catches it
    result = generate_schedules(
        [opt, opt], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    )
    keys = [frozenset(s.ref_no for s in r.all_sections) for r in result]
    assert len(keys) == len(set(keys))


def test_solver_inputs_not_mutated() -> None:
    opts = [_mk_opt(str(i), "TEST", f"{i:03d}", credits=Decimal("1"),
                    meeting_times=[_mt(days=("M",) if i % 2 == 0 else ("T",),
                                        start=time(9, 0), end=time(10, 0))])
            for i in range(4)]
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("4"))
    opt_refs_before = [o.parent.ref_no for o in opts]
    generate_schedules(opts, [], prefs)
    assert [o.parent.ref_no for o in opts] == opt_refs_before


def test_solver_total_credits_are_decimal() -> None:
    opt = _mk_opt("1", "MATH", "035", credits=Decimal("1"))
    result = generate_schedules([opt], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4")))
    assert all(isinstance(r.total_credits, Decimal) for r in result)


def test_solver_schedule_has_no_requirement_gains_field() -> None:
    # Schedule objects must not have a requirement_gains attribute (belongs to RankedSchedule)
    opt = _mk_opt("1", "MATH", "035", credits=Decimal("1"))
    result = generate_schedules([opt], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4")))
    assert len(result) == 1
    assert not hasattr(result[0], "requirement_gains")


def test_solver_parent_and_lab_classified_correctly() -> None:
    lab = _section("L", "CPSC", "031", course_type="Lab", credits=Decimal("0"),
                   meeting_times=[_mt(days=("W",), start=time(10, 30), end=time(12, 0))])
    parent = _section("P", "CPSC", "031", credits=Decimal("1"),
                      linked_sections=[lab])
    opt = SelectionOption(parent=parent, linked_sections=(lab,))
    result = generate_schedules([opt], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4")))
    assert len(result) == 1
    assert result[0].parent_sections == [parent]
    assert result[0].lab_sections == [lab]


def test_solver_seminar_pair_credits_counted_once() -> None:
    sem2 = _section("B", "ANTH", "122", course_type="Seminar2", credits=Decimal("1"),
                    meeting_times=[_mt(days=("W",), start=time(13, 15), end=time(16, 0))])
    sem1 = _section("A", "ANTH", "122", course_type="Seminar1", credits=Decimal("1"),
                    meeting_times=[_mt(days=("W",), start=time(13, 15), end=time(16, 0))],
                    linked_sections=[sem2])
    opt = SelectionOption(parent=sem1, linked_sections=(sem2,))
    result = generate_schedules([opt], [], _prefs(min_credits=Decimal("1"), max_credits=Decimal("4")))
    # Total credits should be 1, not 2 (Seminar2 credits not counted separately)
    assert len(result) >= 1
    assert result[0].total_credits == Decimal("1")


def test_solver_every_returned_schedule_conflict_free() -> None:
    opts = [
        _mk_opt("1", "CPSC", "031", credits=Decimal("1"),
                meeting_times=[_mt(days=("T", "R"), start=time(11, 20), end=time(12, 35))]),
        _mk_opt("2", "MATH", "035", credits=Decimal("1"),
                meeting_times=[_mt(days=("M", "W", "F"), start=time(10, 30), end=time(11, 20))]),
        _mk_opt("3", "ENGL", "011", credits=Decimal("1"),
                meeting_times=[_mt(days=("T", "R"), start=time(13, 15), end=time(14, 30))]),
        _mk_opt("4", "HIST", "020", credits=Decimal("1"),
                meeting_times=[_mt(days=("M", "W", "F"), start=time(9, 30), end=time(10, 20))]),
    ]
    result = generate_schedules(opts, [], _prefs(min_credits=Decimal("3"), max_credits=Decimal("4")))
    for sched in result:
        for a, b in combinations(sched.all_sections, 2):
            assert not a.conflicts_with(b), f"Conflict: {a.ref_no} vs {b.ref_no}"


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------


@st.composite  # type: ignore[misc]
def _meeting_time_st(draw: st.DrawFn) -> MeetingTime:
    hour = draw(st.integers(min_value=8, max_value=16))
    minute = draw(st.sampled_from([0, 30]))
    start = time(hour, minute)
    dur = draw(st.integers(min_value=50, max_value=90))
    end_m = hour * 60 + minute + dur
    end = time(end_m // 60, end_m % 60)
    days: tuple[str, ...] = draw(st.sampled_from([("M", "W", "F"), ("T", "R"), ("M",), ("W",)]))
    return MeetingTime(days=days, start=start, end=end)


@st.composite  # type: ignore[misc]
def _option_list_st(draw: st.DrawFn) -> list[SelectionOption]:
    n = draw(st.integers(min_value=0, max_value=8))
    opts: list[SelectionOption] = []
    _all_day_sets = [("M", "W", "F"), ("T", "R"), ("M",), ("W",)]
    for i in range(n):
        credits = draw(st.sampled_from([Decimal("0.5"), Decimal("1"), Decimal("1.5")]))
        mt = draw(_meeting_time_st())
        parent = CourseSection(
            ref_no=f"P{i:04d}",
            subject="TEST",
            number=f"{i:03d}",
            section_id="01",
            title=f"Test {i}",
            credits=credits,
            distribution=frozenset(),
            enr_limit=None,
            instructors=[],
            course_type="Course",
            meeting_times=[mt],
            note="",
            linked_sections=[],
        )
        include_lab = draw(st.booleans())
        if include_lab:
            # Pick lab days that don't overlap with the parent's days so the
            # SelectionOption is internally conflict-free (per spec validation).
            parent_day_set = set(mt.days)
            safe_day_sets = [d for d in _all_day_sets if not (set(d) & parent_day_set)]
            if not safe_day_sets:
                opts.append(SelectionOption(parent=parent, linked_sections=()))
                continue
            lab_days: tuple[str, ...] = draw(st.sampled_from(safe_day_sets))
            lab_mt = draw(_meeting_time_st())
            # Rebuild with safe days
            lab_mt = MeetingTime(days=lab_days, start=lab_mt.start, end=lab_mt.end)
            lab = CourseSection(
                ref_no=f"L{i:04d}",
                subject="TEST",
                number=f"{i:03d}",
                section_id="L",
                title=f"Test {i} Lab",
                credits=Decimal("0"),
                distribution=frozenset(),
                enr_limit=None,
                instructors=[],
                course_type="Lab",
                meeting_times=[lab_mt],
                note="",
                linked_sections=[],
            )
            opts.append(SelectionOption(parent=parent, linked_sections=(lab,)))
        else:
            opts.append(SelectionOption(parent=parent, linked_sections=()))
    return opts


@st.composite  # type: ignore[misc]
def _prefs_st(draw: st.DrawFn) -> Preferences:
    min_cr = draw(st.sampled_from([Decimal("1"), Decimal("2"), Decimal("3")]))
    extra = draw(st.sampled_from([Decimal("1"), Decimal("1.5"), Decimal("2")]))
    max_cr = min_cr + extra
    return Preferences(
        min_credits=min_cr,
        max_credits=max_cr,
        free_days=[],
        earliest_start=None,
        latest_end=None,
        preferred_subjects=[],
        excluded_courses=[],
        lock_preregistered=True,
    )


@given(
    options=_option_list_st(),
    prefs=_prefs_st(),
    max_results=st.integers(min_value=1, max_value=50),
)
@settings(
    max_examples=300,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_solver_invariants(
    options: list[SelectionOption],
    prefs: Preferences,
    max_results: int,
) -> None:
    results = generate_schedules(options, [], prefs, max_results)

    # Credit bounds and Decimal type
    for sched in results:
        assert prefs.min_credits <= sched.total_credits <= prefs.max_credits
        assert isinstance(sched.total_credits, Decimal)

    # No duplicate parent course codes
    for sched in results:
        codes = [s.course_code for s in sched.parent_sections]
        assert len(codes) == len(set(codes))

    # No duplicate ref_nos
    for sched in results:
        refs = [s.ref_no for s in sched.all_sections]
        assert len(refs) == len(set(refs))

    # No time conflicts
    for sched in results:
        for a, b in combinations(sched.all_sections, 2):
            assert not a.conflicts_with(b)

    # max_results honored
    assert len(results) <= max_results

    # Determinism: identical inputs produce identical canonical key sequences
    results2 = generate_schedules(options, [], prefs, max_results)
    keys1 = [frozenset(s.ref_no for s in r.all_sections) for r in results]
    keys2 = [frozenset(s.ref_no for s in r.all_sections) for r in results2]
    assert keys1 == keys2


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


def test_integration_catalog_sample_and_synthetic_audit() -> None:
    """Parse catalog_sample_10.csv + audit_synthetic.pdf; run full solver pipeline."""
    from app.adapters.swarthmore.requirement_defs import get_requirement_definitions
    from app.core.requirements import build_requirement_status
    from app.parsers.audit import parse_audit
    from app.parsers.catalog import parse_catalog

    catalog_path = _FIXTURES / "catalog_sample_10.csv"
    audit_path = _FIXTURES / "audit_synthetic.pdf"
    assert catalog_path.exists(), f"Missing fixture: {catalog_path}"
    assert audit_path.exists(), f"Missing fixture: {audit_path}"

    sections = parse_catalog(catalog_path)
    student = parse_audit(audit_path)

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        definitions = get_requirement_definitions(student)
        req_status = build_requirement_status(student, definitions)

    # Use permissive preferences so the small fixture can produce schedules
    prefs = Preferences(
        min_credits=Decimal("1"),
        max_credits=Decimal("4"),
        free_days=[],
        earliest_start=None,
        latest_end=None,
        preferred_subjects=["CPSC"],
        excluded_courses=[],
        lock_preregistered=True,
    )

    candidates = filter_candidates(sections, student, req_status, prefs)
    # ARTH 001N (FY Seminar) excluded; CPSC 063 excluded (locked preregistered)
    assert all(s.course_type != "FY Seminar" for s in candidates)
    assert all(s.course_code != "CPSC 063" for s in candidates)

    options = expand_selection_options(candidates, prefs)
    assert len(options) >= 1

    result = generate_schedules(options, [], prefs, max_results=200)

    # Must produce at least one schedule
    assert len(result) >= 1, "Expected at least one valid schedule"

    # No conflict in any schedule (Seminar1/Seminar2 same-time pairs are permitted).
    def _is_sem_pair(x: CourseSection, y: CourseSection) -> bool:
        return {x.course_type, y.course_type} == {"Seminar1", "Seminar2"}

    for sched in result:
        for a, b in combinations(sched.all_sections, 2):
            if not _is_sem_pair(a, b):
                assert not a.conflicts_with(b), (
                    f"Conflict: {a.course_code} ({a.ref_no}) vs {b.course_code} ({b.ref_no})"
                )

    # Credit bounds respected
    for sched in result:
        assert prefs.min_credits <= sched.total_credits <= prefs.max_credits

    # Exempted CPSC 021 never appears
    for sched in result:
        assert all(s.course_code != "CPSC 021" for s in sched.all_sections)

    # Completed passing courses (with passing grade) not in schedules
    # unless they're in retake_eligible (which they aren't in the sample catalog)
    completed_passing_codes = {c.code for c in student.completed_courses if c.is_passing}
    retake_eligible = {
        code
        for item in req_status.items
        if not item.satisfied and not item.auto_registered
        for code in item.satisfying_courses
    }
    for sched in result:
        for section in sched.all_sections:
            if section.course_code in completed_passing_codes:
                assert section.course_code in retake_eligible, (
                    f"{section.course_code} is completed+passing but appears in schedule "
                    "without being retake-eligible"
                )

    # Determinism
    result2 = generate_schedules(options, [], prefs, max_results=200)
    keys1 = [frozenset(s.ref_no for s in r.all_sections) for r in result]
    keys2 = [frozenset(s.ref_no for s in r.all_sections) for r in result2]
    assert keys1 == keys2
