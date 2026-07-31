"""Unit tests for backend/app/models.py — Stage 0 acceptance criteria."""

from datetime import date, time
from decimal import Decimal

import pytest

from app.models import (
    CompletedCourse,
    ConstraintDiagnostic,
    CourseSection,
    MeetingTime,
    Preferences,
    RankedSchedule,
    RequirementItem,
    RequirementStatus,
    Schedule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_section(
    subject: str = "CPSC",
    number: str = "035",
    section_id: str = "01",
    course_type: str = "Course",
    credits: str = "1",
    distribution: frozenset[str] = frozenset(),
    meeting_times: list[MeetingTime] | None = None,
    linked_sections: list[CourseSection] | None = None,
    ref_no: str = "99999",
) -> CourseSection:
    return CourseSection(
        ref_no=ref_no,
        subject=subject,
        number=number,
        section_id=section_id,
        title="Test Course",
        credits=Decimal(credits),
        distribution=distribution,
        enr_limit=None,
        instructors=[],
        course_type=course_type,
        meeting_times=meeting_times or [],
        note="",
        linked_sections=linked_sections or [],
    )


def mt(days: str, start: tuple[int, int], end: tuple[int, int]) -> MeetingTime:
    """Build a MeetingTime from a concatenated day string and (hour, minute) tuples."""
    return MeetingTime(
        days=tuple(days),
        start=time(*start),
        end=time(*end),
    )


# ---------------------------------------------------------------------------
# MeetingTime.conflicts_with
# ---------------------------------------------------------------------------


class TestMeetingTimeConflicts:
    def test_identical_times_conflict(self) -> None:
        a = mt("TR", (11, 20), (12, 35))
        assert a.conflicts_with(a)

    def test_same_time_same_days_conflict(self) -> None:
        a = mt("MWF", (10, 30), (11, 20))
        b = mt("MWF", (10, 30), (11, 20))
        assert a.conflicts_with(b)
        assert b.conflicts_with(a)

    def test_back_to_back_no_conflict(self) -> None:
        # 9:00–10:00 followed by 10:00–11:00 on same days
        a = mt("MWF", (9, 0), (10, 0))
        b = mt("MWF", (10, 0), (11, 0))
        assert not a.conflicts_with(b)
        assert not b.conflicts_with(a)

    def test_different_days_no_conflict(self) -> None:
        a = mt("MWF", (10, 30), (11, 20))
        b = mt("TR", (10, 30), (11, 20))
        assert not a.conflicts_with(b)

    def test_one_minute_overlap_conflicts(self) -> None:
        # 10:00–11:01 and 11:00–12:00 overlap by one minute
        a = mt("MW", (10, 0), (11, 1))
        b = mt("MW", (11, 0), (12, 0))
        assert a.conflicts_with(b)
        assert b.conflicts_with(a)

    def test_partial_day_overlap_conflicts(self) -> None:
        # Both on Monday but different days listed; Monday is the shared day
        a = mt("MWF", (9, 0), (10, 15))
        b = mt("M", (10, 0), (11, 0))
        assert a.conflicts_with(b)

    def test_no_shared_days_even_if_time_overlaps(self) -> None:
        a = mt("MWF", (9, 0), (10, 15))
        b = mt("TR", (9, 0), (10, 15))
        assert not a.conflicts_with(b)

    def test_symmetry(self) -> None:
        a = mt("TR", (11, 20), (12, 35))
        b = mt("TR", (12, 0), (13, 15))
        assert a.conflicts_with(b) == b.conflicts_with(a)

    def test_contained_interval_conflicts(self) -> None:
        # b is entirely within a
        a = mt("MW", (9, 0), (12, 0))
        b = mt("MW", (10, 0), (11, 0))
        assert a.conflicts_with(b)


# ---------------------------------------------------------------------------
# CourseSection.conflicts_with (multi-meeting)
# ---------------------------------------------------------------------------


class TestCourseSectionConflicts:
    def test_single_meeting_conflict(self) -> None:
        a = make_section(meeting_times=[mt("TR", (11, 20), (12, 35))])
        b = make_section(meeting_times=[mt("TR", (11, 20), (12, 35))])
        assert a.conflicts_with(b)

    def test_single_meeting_no_conflict(self) -> None:
        a = make_section(meeting_times=[mt("MWF", (10, 30), (11, 20))])
        b = make_section(meeting_times=[mt("TR", (10, 30), (11, 20))])
        assert not a.conflicts_with(b)

    def test_multi_meeting_conflict_via_second_slot(self) -> None:
        # Lab has two meeting time entries; second one conflicts with b
        a = make_section(
            course_type="Lab",
            credits="0",
            meeting_times=[
                mt("M", (9, 0), (10, 0)),   # no conflict with b
                mt("W", (10, 30), (12, 0)),  # conflicts with b
            ],
        )
        b = make_section(meeting_times=[mt("MW", (10, 30), (11, 45))])
        assert a.conflicts_with(b)

    def test_multi_meeting_no_conflict(self) -> None:
        a = make_section(
            course_type="Lab",
            credits="0",
            meeting_times=[
                mt("M", (9, 0), (10, 0)),
                mt("W", (9, 0), (10, 0)),
            ],
        )
        b = make_section(meeting_times=[mt("TR", (10, 30), (11, 45))])
        assert not a.conflicts_with(b)

    def test_no_meeting_times_no_conflict(self) -> None:
        a = make_section(meeting_times=[])
        b = make_section(meeting_times=[mt("TR", (10, 30), (11, 45))])
        assert not a.conflicts_with(b)


# ---------------------------------------------------------------------------
# CourseSection properties
# ---------------------------------------------------------------------------


class TestCourseSectionProperties:
    def test_course_code(self) -> None:
        s = make_section(subject="MATH", number="027")
        assert s.course_code == "MATH 027"

    def test_is_parent_course_type(self) -> None:
        for t in ("Course", "Language Course", "Studio Course", "FY Seminar", "Workshop"):
            assert make_section(course_type=t).is_parent

    def test_is_not_parent_lab(self) -> None:
        assert not make_section(course_type="Lab", credits="0").is_parent

    def test_is_not_parent_drill(self) -> None:
        assert not make_section(course_type="Drill", credits="0").is_parent


# ---------------------------------------------------------------------------
# CompletedCourse properties
# ---------------------------------------------------------------------------


class TestCompletedCourse:
    def _cc(self, grade: str) -> CompletedCourse:
        return CompletedCourse(
            code="CPSC 031",
            title="Intro to Computer Systems",
            grade=grade,
            credits=Decimal("1"),
            term="Fall 2023",
        )

    def test_is_passing_letter_grades(self) -> None:
        for g in ("A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D"):
            assert self._cc(g).is_passing, f"Expected {g!r} to be passing"

    def test_is_passing_cr(self) -> None:
        assert self._cc("CR").is_passing

    def test_is_passing_s(self) -> None:
        assert self._cc("S").is_passing

    def test_not_passing_w(self) -> None:
        assert not self._cc("W").is_passing

    def test_not_passing_nc(self) -> None:
        assert not self._cc("NC").is_passing

    def test_not_passing_f(self) -> None:
        assert not self._cc("F").is_passing

    def test_not_passing_nr(self) -> None:
        assert not self._cc("NR").is_passing

    def test_is_letter_grade_b_plus(self) -> None:
        assert self._cc("B+").is_letter_grade

    def test_is_letter_grade_c(self) -> None:
        assert self._cc("C").is_letter_grade

    def test_not_letter_grade_cr(self) -> None:
        assert not self._cc("CR").is_letter_grade

    def test_not_letter_grade_s(self) -> None:
        assert not self._cc("S").is_letter_grade

    def test_not_letter_grade_w(self) -> None:
        assert not self._cc("W").is_letter_grade

    def test_not_letter_grade_preregistered(self) -> None:
        assert not self._cc("----").is_letter_grade


# ---------------------------------------------------------------------------
# Schedule.all_sections
# ---------------------------------------------------------------------------


class TestScheduleAllSections:
    def test_all_sections_combines_parents_and_labs(self) -> None:
        parent = make_section(ref_no="1", meeting_times=[mt("TR", (11, 20), (12, 35))])
        lab = make_section(ref_no="2", course_type="Lab", credits="0",
                           meeting_times=[mt("W", (10, 30), (12, 0))])
        schedule = Schedule(
            parent_sections=[parent],
            lab_sections=[lab],
            total_credits=Decimal("1"),
        )
        assert schedule.all_sections == [parent, lab]

    def test_all_sections_no_labs(self) -> None:
        parent = make_section(ref_no="1")
        schedule = Schedule(
            parent_sections=[parent],
            lab_sections=[],
            total_credits=Decimal("1"),
        )
        assert schedule.all_sections == [parent]

    def test_all_sections_empty_schedule(self) -> None:
        schedule = Schedule(
            parent_sections=[],
            lab_sections=[],
            total_credits=Decimal("0"),
        )
        assert schedule.all_sections == []


# ---------------------------------------------------------------------------
# RequirementStatus.items_satisfied_by
# ---------------------------------------------------------------------------


class TestItemsSatisfiedBy:
    def _make_status(self, items: list[RequirementItem]) -> RequirementStatus:
        return RequirementStatus(items=items, credits_remaining=Decimal("0.5"))

    def test_attribute_match(self) -> None:
        item = RequirementItem(
            id="writing",
            label="Writing",
            satisfied=False,
            satisfying_courses=[],
            matching_attributes=frozenset({"HUW", "NSW"}),
            notes="",
        )
        status = self._make_status([item])
        section = make_section(
            distribution=frozenset({"HU", "W", "HUW"}),
        )
        assert item in status.items_satisfied_by(section)

    def test_code_match(self) -> None:
        item = RequirementItem(
            id="cpsc031",
            label="CPSC 031",
            satisfied=False,
            satisfying_courses=["CPSC 031"],
            matching_attributes=frozenset(),
            notes="",
        )
        status = self._make_status([item])
        section = make_section(subject="CPSC", number="031")
        assert item in status.items_satisfied_by(section)

    def test_subject_predicate_match(self) -> None:
        item = RequirementItem(
            id="cpsc_credits",
            label="CPSC Credits",
            satisfied=False,
            satisfying_courses=[],
            matching_attributes=frozenset(),
            notes="",
            subject_predicate="CPSC",
        )
        status = self._make_status([item])
        section = make_section(subject="CPSC", number="043", credits="1")
        assert item in status.items_satisfied_by(section)

    def test_subject_predicate_requires_one_credit(self) -> None:
        item = RequirementItem(
            id="cpsc_credits",
            label="CPSC Credits",
            satisfied=False,
            satisfying_courses=[],
            matching_attributes=frozenset(),
            notes="",
            subject_predicate="CPSC",
        )
        status = self._make_status([item])
        lab = make_section(subject="CPSC", number="031", course_type="Lab", credits="0")
        assert item not in status.items_satisfied_by(lab)

    def test_already_satisfied_excluded(self) -> None:
        item = RequirementItem(
            id="writing",
            label="Writing",
            satisfied=True,  # already done
            satisfying_courses=[],
            matching_attributes=frozenset({"HUW"}),
            notes="",
        )
        status = self._make_status([item])
        section = make_section(distribution=frozenset({"HU", "W", "HUW"}))
        assert status.items_satisfied_by(section) == []

    def test_auto_registered_excluded(self) -> None:
        item = RequirementItem(
            id="cs_senior_comp",
            label="CPSC 099",
            satisfied=False,
            satisfying_courses=["CPSC 099"],
            matching_attributes=frozenset(),
            notes="",
            auto_registered=True,
        )
        status = self._make_status([item])
        section = make_section(subject="CPSC", number="099")
        assert status.items_satisfied_by(section) == []

    def test_no_match(self) -> None:
        item = RequirementItem(
            id="writing",
            label="Writing",
            satisfied=False,
            satisfying_courses=[],
            matching_attributes=frozenset({"HUW"}),
            notes="",
        )
        status = self._make_status([item])
        section = make_section(distribution=frozenset({"SS"}))
        assert status.items_satisfied_by(section) == []


# ---------------------------------------------------------------------------
# RequirementItem dataclass field ordering (ensure no TypeError on construction)
# ---------------------------------------------------------------------------


class TestRequirementItemConstruction:
    def test_minimal_construction(self) -> None:
        item = RequirementItem(
            id="x",
            label="X",
            satisfied=False,
            satisfying_courses=[],
            matching_attributes=frozenset(),
            notes="",
        )
        assert item.subject_predicate is None
        assert item.auto_registered is False

    def test_with_subject_predicate(self) -> None:
        item = RequirementItem(
            id="cpsc",
            label="CPSC Credits",
            satisfied=False,
            satisfying_courses=[],
            matching_attributes=frozenset(),
            notes="",
            subject_predicate="CPSC",
        )
        assert item.subject_predicate == "CPSC"

    def test_with_auto_registered(self) -> None:
        item = RequirementItem(
            id="cpsc099",
            label="Senior Comp",
            satisfied=False,
            satisfying_courses=["CPSC 099"],
            matching_attributes=frozenset(),
            notes="",
            auto_registered=True,
        )
        assert item.auto_registered is True


# ---------------------------------------------------------------------------
# MeetingTime immutability
# ---------------------------------------------------------------------------


class TestMeetingTimeImmutability:
    def test_frozen(self) -> None:
        mt_obj = mt("TR", (11, 20), (12, 35))
        with pytest.raises((AttributeError, TypeError)):
            mt_obj.days = ("M",)  # type: ignore[misc]

    def test_hashable(self) -> None:
        mt_obj = mt("TR", (11, 20), (12, 35))
        s = {mt_obj, mt_obj}
        assert len(s) == 1


# ---------------------------------------------------------------------------
# Decimal type enforcement (spot checks)
# ---------------------------------------------------------------------------


class TestDecimalEnforcement:
    def test_schedule_total_credits_is_decimal(self) -> None:
        s = Schedule(
            parent_sections=[],
            lab_sections=[],
            total_credits=Decimal("3"),
        )
        assert isinstance(s.total_credits, Decimal)

    def test_preferences_credit_defaults_are_decimal(self) -> None:
        p = Preferences()
        assert isinstance(p.min_credits, Decimal)
        assert isinstance(p.max_credits, Decimal)
        assert p.min_credits == Decimal("3")
        assert p.max_credits == Decimal("4")
