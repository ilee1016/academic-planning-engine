"""Tests for app/api_models.py: request validation and response conversion."""
from __future__ import annotations

import json
from datetime import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api_models import (
    PreferencesRequest,
    _schedule_id,
    diagnostic_to_response,
    preferences_from_request,
    ranked_schedule_to_response,
    requirement_summary,
    student_summary,
)
from app.models import (
    CompletedCourse,
    ConstraintDiagnostic,
    CourseSection,
    MeetingTime,
    Preferences,
    RankedSchedule,
    RequirementBlock,
    RequirementItem,
    RequirementStatus,
    Schedule,
    StudentRecord,
)
from datetime import date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prefs(**kwargs: object) -> PreferencesRequest:
    defaults: dict[str, object] = {
        "min_credits": "3",
        "max_credits": "4",
        "lock_preregistered": True,
    }
    defaults.update(kwargs)
    return PreferencesRequest.model_validate(defaults)


def _section(ref_no: str, subject: str, number: str, *, credits: str = "1") -> CourseSection:
    return CourseSection(
        ref_no=ref_no, subject=subject, number=number, section_id="01",
        title=f"{subject} {number}", credits=Decimal(credits),
        distribution=frozenset(), enr_limit=None, instructors=[],
        course_type="Course",
        meeting_times=[MeetingTime(days=("T", "R"), start=time(10, 0), end=time(11, 0))],
        note="", linked_sections=[],
    )


def _sched(parents: list[CourseSection]) -> Schedule:
    total = sum(s.credits for s in parents)
    return Schedule(parent_sections=parents, lab_sections=[], total_credits=total)


def _ranked(schedule: Schedule) -> RankedSchedule:
    return RankedSchedule(
        schedule=schedule,
        category="balanced",
        score=100.0,
        score_breakdown={
            "requirement_gains": 0.0,
            "preferred_subjects": 0.0,
            "free_days": 5.0,
            "compactness": 85.0,
            "credit_load": 10.0,
        },
        requirement_gains=[],
        explanation="",
    )


def _student() -> StudentRecord:
    return StudentRecord(
        name="Demo, Student",
        student_id="000000000",
        major="Computer Science",
        class_year=2027,
        catalog_year="202304",
        credits_required=Decimal("32"),
        credits_applied=Decimal("31.5"),
        audit_date=date(2026, 7, 1),
        completed_courses=[],
        preregistered_courses=[],
        other_courses=[],
        exempted_courses=[],
        requirement_blocks=[],
        exceptions=[],
    )


# ---------------------------------------------------------------------------
# 1. Valid preferences parse
# ---------------------------------------------------------------------------


def test_valid_preferences_parse() -> None:
    """Test 1: Valid preferences round-trip successfully."""
    prefs = _prefs(
        min_credits="3",
        max_credits="4",
        free_days=["F"],
        preferred_subjects=["CPSC"],
        excluded_courses=["PHED 001A"],
        earliest_start="09:00",
        latest_end="17:00",
    )
    assert prefs.min_credits == Decimal("3")
    assert prefs.max_credits == Decimal("4")
    assert prefs.free_days == ["F"]
    assert prefs.preferred_subjects == ["CPSC"]


# ---------------------------------------------------------------------------
# 2. min_credits > max_credits rejected
# ---------------------------------------------------------------------------


def test_min_greater_than_max_rejected() -> None:
    """Test 2: min_credits > max_credits raises ValidationError."""
    with pytest.raises(ValidationError):
        _prefs(min_credits="5", max_credits="3")


# ---------------------------------------------------------------------------
# 3. Negative credits rejected
# ---------------------------------------------------------------------------


def test_negative_credits_rejected() -> None:
    """Test 3: Negative min_credits is rejected."""
    with pytest.raises(ValidationError):
        _prefs(min_credits="-1", max_credits="4")


# ---------------------------------------------------------------------------
# 4. Invalid weekday rejected
# ---------------------------------------------------------------------------


def test_invalid_weekday_rejected() -> None:
    """Test 4: Unrecognized weekday code raises ValidationError."""
    with pytest.raises(ValidationError):
        _prefs(free_days=["X"])


def test_sunday_rejected() -> None:
    """Test 4b: 'S' is not a valid weekday in this system."""
    with pytest.raises(ValidationError):
        _prefs(free_days=["S"])


# ---------------------------------------------------------------------------
# 5. Duplicate weekdays normalized
# ---------------------------------------------------------------------------


def test_duplicate_weekdays_normalized() -> None:
    """Test 5: Duplicate free day entries are deduplicated while preserving order."""
    prefs = _prefs(free_days=["F", "F", "M", "M"])
    assert prefs.free_days == ["F", "M"]


# ---------------------------------------------------------------------------
# 6. Duplicate preferred subjects normalized
# ---------------------------------------------------------------------------


def test_duplicate_preferred_subjects_normalized() -> None:
    """Test 6: Duplicate subjects are deduplicated."""
    prefs = _prefs(preferred_subjects=["CPSC", "CPSC", "MATH"])
    assert prefs.preferred_subjects == ["CPSC", "MATH"]


# ---------------------------------------------------------------------------
# 7. Course codes normalized
# ---------------------------------------------------------------------------


def test_excluded_courses_normalized_to_uppercase() -> None:
    """Test 7: Course codes are normalized to uppercase."""
    prefs = _prefs(excluded_courses=["cpsc 035", "math 021"])
    assert "CPSC 035" in prefs.excluded_courses
    assert "MATH 021" in prefs.excluded_courses


# ---------------------------------------------------------------------------
# 8. Invalid time window rejected
# ---------------------------------------------------------------------------


def test_invalid_time_window_rejected() -> None:
    """Test 8: earliest_start >= latest_end raises ValidationError."""
    with pytest.raises(ValidationError):
        _prefs(earliest_start="15:00", latest_end="10:00")


def test_equal_times_rejected() -> None:
    """Test 8b: earliest_start == latest_end is invalid."""
    with pytest.raises(ValidationError):
        _prefs(earliest_start="10:00", latest_end="10:00")


def test_invalid_time_format_rejected() -> None:
    """Test 8c: Non-HH:MM time string raises ValidationError."""
    with pytest.raises(ValidationError):
        _prefs(earliest_start="10:00am")


# ---------------------------------------------------------------------------
# 9. Excessively large lists rejected
# ---------------------------------------------------------------------------


def test_too_many_free_days_rejected() -> None:
    """Test 9: Lists exceeding _MAX_LIST_LEN are rejected."""
    with pytest.raises(ValidationError):
        _prefs(free_days=["M"] * 25)


def test_too_many_subjects_rejected() -> None:
    """Test 9b: preferred_subjects list too long raises ValidationError."""
    with pytest.raises(ValidationError):
        _prefs(preferred_subjects=[f"SUBJ{i:02d}" for i in range(25)])


# ---------------------------------------------------------------------------
# 10. Conversion preserves Decimal
# ---------------------------------------------------------------------------


def test_preferences_conversion_preserves_decimal() -> None:
    """Test 10: preferences_from_request returns domain Preferences with Decimal credits."""
    req = _prefs(min_credits="3.5", max_credits="4.0")
    domain = preferences_from_request(req)
    assert isinstance(domain.min_credits, Decimal)
    assert isinstance(domain.max_credits, Decimal)
    assert domain.min_credits == Decimal("3.5")
    assert domain.max_credits == Decimal("4.0")


# ---------------------------------------------------------------------------
# 11. Response credits serialize as strings
# ---------------------------------------------------------------------------


def test_ranked_schedule_credits_serialized_as_string() -> None:
    """Test 11: total_credits in ranked schedule response is a string, not float."""
    s = _section("1", "CPSC", "035", credits="1.5")
    sched = _sched([s])
    rs = _ranked(sched)
    resp = ranked_schedule_to_response(rs)
    assert resp.total_credits == "1.5"
    assert isinstance(resp.total_credits, str)
    # Section credits are also strings
    assert resp.parent_sections[0].credits == "1.5"


# ---------------------------------------------------------------------------
# 12. Internal fields omitted from student summary
# ---------------------------------------------------------------------------


def test_student_summary_omits_name_and_id() -> None:
    """Test 12: student_summary() excludes student name and student_id."""
    st = _student()
    resp = student_summary(st)
    d = resp.model_dump()
    assert "name" not in d
    assert "student_id" not in d
    assert d["major"] == "Computer Science"
    assert d["class_year"] == 2027


def test_student_summary_json_no_name_or_id() -> None:
    """Test 12b: Serialized JSON contains no name or student_id."""
    st = _student()
    resp = student_summary(st)
    body = json.dumps(resp.model_dump())
    assert "Demo" not in body
    assert "000000000" not in body


# ---------------------------------------------------------------------------
# 13. Requirement summary
# ---------------------------------------------------------------------------


def test_requirement_summary_counts() -> None:
    item_a = RequirementItem(
        id="a", label="A", satisfied=False, satisfying_courses=[], matching_attributes=frozenset(), notes=""
    )
    item_b = RequirementItem(
        id="b", label="B", satisfied=True, satisfying_courses=[], matching_attributes=frozenset(), notes=""
    )
    status = RequirementStatus(items=[item_a, item_b], credits_remaining=Decimal("1"))
    resp = requirement_summary(status, [])
    assert resp.total_items == 2
    assert resp.unsatisfied_items == 1


def test_requirement_summary_unmatched_ids() -> None:
    status = RequirementStatus(items=[], credits_remaining=Decimal("0"))
    resp = requirement_summary(status, ["orphan_id"])
    assert resp.unmatched_items == ["orphan_id"]


# ---------------------------------------------------------------------------
# 14. Schedule ID is deterministic and identity-free
# ---------------------------------------------------------------------------


def test_schedule_id_deterministic() -> None:
    """Schedule ID must be the same for the same section set."""
    s = _section("42", "CPSC", "035")
    sched = _sched([s])
    id1 = _schedule_id(sched)
    id2 = _schedule_id(sched)
    assert id1 == id2


def test_schedule_id_differs_for_different_labs() -> None:
    """Different lab sections produce different schedule IDs."""
    parent = _section("1", "CPSC", "031")
    lab_a = CourseSection(
        ref_no="2", subject="CPSC", number="031", section_id="L1", title="Lab",
        credits=Decimal("0"), distribution=frozenset(), enr_limit=None, instructors=[],
        course_type="Lab",
        meeting_times=[MeetingTime(days=("M",), start=time(10, 0), end=time(12, 0))],
        note="", linked_sections=[],
    )
    lab_b = CourseSection(
        ref_no="3", subject="CPSC", number="031", section_id="L2", title="Lab",
        credits=Decimal("0"), distribution=frozenset(), enr_limit=None, instructors=[],
        course_type="Lab",
        meeting_times=[MeetingTime(days=("W",), start=time(10, 0), end=time(12, 0))],
        note="", linked_sections=[],
    )
    sched_a = Schedule(parent_sections=[parent], lab_sections=[lab_a], total_credits=Decimal("1"))
    sched_b = Schedule(parent_sections=[parent], lab_sections=[lab_b], total_credits=Decimal("1"))
    assert _schedule_id(sched_a) != _schedule_id(sched_b)


def test_schedule_id_no_student_data() -> None:
    """Schedule ID must not contain student name or ID."""
    s = _section("99", "CPSC", "035")
    sched = _sched([s])
    sid = _schedule_id(sched)
    assert "Demo" not in sid
    assert "000000000" not in sid


# ---------------------------------------------------------------------------
# 15. Diagnostic conversion
# ---------------------------------------------------------------------------


def test_diagnostic_to_response_preserves_fields() -> None:
    diag = ConstraintDiagnostic(
        no_valid_schedules=True,
        reasons=["Too few options."],
        suggested_relaxations=["Lower minimum credits."],
    )
    resp = diagnostic_to_response(diag)
    assert resp.no_valid_schedules is True
    assert resp.reasons == ["Too few options."]
    assert resp.suggested_relaxations == ["Lower minimum credits."]


# ---------------------------------------------------------------------------
# 16. ranked_schedule_to_response includes all fields
# ---------------------------------------------------------------------------


def test_ranked_schedule_response_structure() -> None:
    s = _section("1", "CPSC", "035")
    sched = _sched([s])
    rs = _ranked(sched)
    resp = ranked_schedule_to_response(rs)
    assert resp.schedule_id
    assert resp.category == "balanced"
    assert resp.score == 100.0
    assert resp.explanation == ""
    d = resp.model_dump()
    assert "requirement_gains" in d
    assert "score_breakdown" in d
    assert "parent_sections" in d
    assert "linked_sections" in d


# ---------------------------------------------------------------------------
# 17. max_credits ceiling
# ---------------------------------------------------------------------------


def test_max_credits_ceiling_enforced() -> None:
    """max_credits > 8 is rejected."""
    with pytest.raises(ValidationError):
        _prefs(min_credits="1", max_credits="9")
