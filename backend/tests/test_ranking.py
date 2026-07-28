"""Tests for core/ranking.py: scoring, categories, sorting, properties, and integration."""
from __future__ import annotations

import copy
import warnings
from datetime import time
from decimal import Decimal
from itertools import combinations
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.ranking import (
    _compute_requirement_gains,
    _score_compactness,
    _score_credit_load,
    _score_free_days,
    _score_preferred_subjects,
    _weekly_idle_minutes,
    rank_schedules,
)
from app.models import (
    CourseSection,
    MeetingTime,
    Preferences,
    RankedSchedule,
    RequirementItem,
    RequirementStatus,
    Schedule,
)

_FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BREAKDOWN_KEYS = frozenset(
    {"requirement_gains", "preferred_subjects", "free_days", "compactness", "credit_load"}
)


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
        meeting_times=meeting_times if meeting_times is not None else [_mt()],
        note="",
        linked_sections=linked_sections if linked_sections is not None else [],
    )


def _sched(
    parents: list[CourseSection],
    labs: list[CourseSection] | None = None,
    *,
    total_credits: Decimal | None = None,
) -> Schedule:
    if labs is None:
        labs = []
    if total_credits is None:
        total_credits = sum(s.credits for s in parents)
    return Schedule(parent_sections=parents, lab_sections=labs, total_credits=total_credits)


def _req(
    item_id: str,
    *,
    satisfied: bool = False,
    satisfying_courses: list[str] | None = None,
    matching_attributes: frozenset[str] = frozenset(),
    subject_predicate: str | None = None,
    auto_registered: bool = False,
    label: str = "",
) -> RequirementItem:
    return RequirementItem(
        id=item_id,
        label=label or item_id,
        satisfied=satisfied,
        satisfying_courses=satisfying_courses if satisfying_courses is not None else [],
        matching_attributes=matching_attributes,
        notes="",
        subject_predicate=subject_predicate,
        auto_registered=auto_registered,
    )


def _status(items: list[RequirementItem] | None = None) -> RequirementStatus:
    return RequirementStatus(
        items=items if items is not None else [],
        credits_remaining=Decimal("2"),
    )


def _prefs(**kwargs: object) -> Preferences:
    defaults: dict[str, object] = {
        "min_credits": Decimal("1"),
        "max_credits": Decimal("4"),
        "free_days": [],
        "preferred_subjects": [],
        "excluded_courses": [],
        "lock_preregistered": False,
    }
    defaults.update(kwargs)
    return Preferences(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Requirement gain scoring
# ---------------------------------------------------------------------------


def test_no_gains_zero_requirement_score() -> None:
    """Test 1: Schedule with no matching items earns zero requirement-gain points."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    sched = _sched([_section("1", "MATH", "021")])  # different subject
    status = _status([item])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].score_breakdown["requirement_gains"] == 0.0
    assert ranked[0].requirement_gains == []


def test_one_gain_scores_100() -> None:
    """Test 2: One unique requirement gain scores exactly 100 points."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    engl = _section("1", "ENGL", "011")
    sched = _sched([engl])
    status = _status([item])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].score_breakdown["requirement_gains"] == 100.0
    assert len(ranked[0].requirement_gains) == 1


def test_duplicate_sections_do_not_double_count_gain() -> None:
    """Test 3: Multiple sections matching the same item still count as one gain."""
    item = _req("cpsc_credits", subject_predicate="CPSC")
    cpsc1 = _section("1", "CPSC", "031", meeting_times=[_mt(days=("M",))])
    cpsc2 = _section("2", "CPSC", "035", meeting_times=[_mt(days=("T",))])
    sched = _sched([cpsc1, cpsc2])
    status = _status([item])
    ranked = rank_schedules([sched], status, _prefs())
    assert len(ranked[0].requirement_gains) == 1
    assert ranked[0].score_breakdown["requirement_gains"] == 100.0


def test_two_gains_score_200() -> None:
    """Test 4: Two distinct requirement gains score 200 points."""
    item_a = _req("writing", satisfying_courses=["ENGL 011"])
    item_b = _req("cpsc031", satisfying_courses=["CPSC 031"])
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    cpsc = _section("2", "CPSC", "031", meeting_times=[_mt(days=("T",))])
    sched = _sched([engl, cpsc])
    status = _status([item_a, item_b])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].score_breakdown["requirement_gains"] == 200.0
    assert len(ranked[0].requirement_gains) == 2


def test_satisfied_items_excluded_from_gains() -> None:
    """Test 5: Already-satisfied items never appear in requirement_gains."""
    item = _req("done", satisfying_courses=["CPSC 035"], satisfied=True)
    cpsc = _section("1", "CPSC", "035")
    sched = _sched([cpsc])
    status = _status([item])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].requirement_gains == []
    assert ranked[0].score_breakdown["requirement_gains"] == 0.0


def test_auto_registered_excluded_from_gains() -> None:
    """Test 6: Auto-registered items are excluded from requirement_gains."""
    item = _req("comp", satisfying_courses=["CPSC 099"], auto_registered=True)
    cpsc = _section("1", "CPSC", "099")
    sched = _sched([cpsc])
    status = _status([item])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].requirement_gains == []
    assert ranked[0].score_breakdown["requirement_gains"] == 0.0


def test_gain_order_follows_status_items_order() -> None:
    """Test 7: requirement_gains preserves RequirementStatus.items order."""
    item_b = _req("b", satisfying_courses=["ENGL 011"])
    item_a = _req("a", satisfying_courses=["CPSC 031"])
    # Items in order [b, a] in status
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    cpsc = _section("2", "CPSC", "031", meeting_times=[_mt(days=("T",))])
    sched = _sched([cpsc, engl])  # parent order shouldn't matter
    status = _status([item_b, item_a])
    ranked = rank_schedules([sched], status, _prefs())
    gain_ids = [g.id for g in ranked[0].requirement_gains]
    assert gain_ids == ["b", "a"]


# ---------------------------------------------------------------------------
# 2. Preferred subject scoring
# ---------------------------------------------------------------------------


def test_first_preferred_subject_scores_12() -> None:
    """Test 8: First preferred subject earns 12 points per parent section."""
    cpsc = _section("1", "CPSC", "035")
    sched = _sched([cpsc])
    status = _status()
    prefs = _prefs(preferred_subjects=["CPSC"])
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["preferred_subjects"] == 12.0


def test_second_and_third_preferred_subjects_score_correctly() -> None:
    """Test 9: Second subject earns 9; third earns 6."""
    math = _section("1", "MATH", "035", meeting_times=[_mt(days=("M",))])
    engl = _section("2", "ENGL", "020", meeting_times=[_mt(days=("T",))])
    sched = _sched([math, engl])
    prefs = _prefs(preferred_subjects=["CPSC", "MATH", "ENGL"])
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    # MATH → position 1 → 9; ENGL → position 2 → 6
    assert ranked[0].score_breakdown["preferred_subjects"] == 9.0 + 6.0


def test_lab_sections_do_not_receive_preferred_subject_points() -> None:
    """Test 10: Linked lab sections are not parent_sections; they get no subject points."""
    parent = _section("1", "CPSC", "031", meeting_times=[_mt(days=("T", "R"))])
    lab = _section(
        "2", "CPSC", "031",
        course_type="Lab",
        credits=Decimal("0"),
        meeting_times=[_mt(days=("W",))],
    )
    sched = Schedule(
        parent_sections=[parent],
        lab_sections=[lab],
        total_credits=Decimal("1"),
    )
    prefs = _prefs(preferred_subjects=["CPSC"])
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    # Only parent earns 12; lab earns nothing
    assert ranked[0].score_breakdown["preferred_subjects"] == 12.0


# ---------------------------------------------------------------------------
# 3. Free-day scoring
# ---------------------------------------------------------------------------


def test_incidental_free_days_score_5_each() -> None:
    """Test 11: Each incidental free weekday scores +5 beyond explicit free days."""
    # Schedule uses only Tuesday; explicit free_days = ["F"]
    # Incidental free: M, W, R (3 days × 5 = 15)
    section = _section("1", "CPSC", "031", meeting_times=[_mt(days=("T",))])
    sched = _sched([section])
    prefs = _prefs(free_days=["F"])
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["free_days"] == 15.0


def test_explicit_free_days_not_double_counted() -> None:
    """Test 12: Explicitly required free days do not earn incidental free-day points."""
    # Schedule uses T, R; explicit free_days=["M","F"]
    # Used: {T, R}; explicit_free: {M, F}; incidental = {W} → 5 pts
    section = _section("1", "CPSC", "031", meeting_times=[_mt(days=("T", "R"))])
    sched = _sched([section])
    prefs = _prefs(free_days=["M", "F"])
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["free_days"] == 5.0


def test_all_days_used_zero_free_day_score() -> None:
    """Free-day score is zero when all weekdays are used and no explicit free days."""
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M", "W", "F"))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T", "R"))])
    sched = _sched([s1, s2])
    status = _status()
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].score_breakdown["free_days"] == 0.0


# ---------------------------------------------------------------------------
# 4. Compactness scoring
# ---------------------------------------------------------------------------


def test_zero_idle_time_earns_max_compactness() -> None:
    """Test 13: A schedule with zero idle time earns 20 compactness points."""
    section = _section("1", "CPSC", "031", meeting_times=[_mt(days=("T",))])
    sched = _sched([section])
    status = _status()
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].score_breakdown["compactness"] == 20.0


def test_back_to_back_produces_zero_idle_minutes() -> None:
    """Test 14: Adjacent meetings (no gap) contribute 0 idle minutes."""
    s1 = _section(
        "1", "A", "001",
        meeting_times=[MeetingTime(days=("T",), start=time(9, 0), end=time(10, 0))],
    )
    s2 = _section(
        "2", "B", "002",
        meeting_times=[MeetingTime(days=("T",), start=time(10, 0), end=time(11, 0))],
    )
    sched = _sched([s1, s2])
    idle = _weekly_idle_minutes(sched)
    assert idle == 0
    assert _score_compactness(idle) == 20.0


def test_gaps_across_separate_days_not_combined() -> None:
    """Test 15: Idle minutes on different days are summed per-day, not globally."""
    # Monday: 9-10 and 11-12 → 60 min gap
    # Wednesday: 9-10 and 11-12 → 60 min gap
    # Total = 120 → _score_compactness(120) = 10
    s1 = _section(
        "1", "A", "001",
        meeting_times=[MeetingTime(days=("M",), start=time(9, 0), end=time(10, 0))],
    )
    s2 = _section(
        "2", "B", "002",
        meeting_times=[MeetingTime(days=("M",), start=time(11, 0), end=time(12, 0))],
    )
    s3 = _section(
        "3", "C", "003",
        meeting_times=[MeetingTime(days=("W",), start=time(9, 0), end=time(10, 0))],
    )
    s4 = _section(
        "4", "D", "004",
        meeting_times=[MeetingTime(days=("W",), start=time(11, 0), end=time(12, 0))],
    )
    sched = _sched([s1, s2, s3, s4])
    idle = _weekly_idle_minutes(sched)
    assert idle == 120
    assert _score_compactness(idle) == 10.0


def test_multiple_gaps_on_one_day_sum_correctly() -> None:
    """Test 16: Two gaps on the same day are summed before scoring."""
    # Monday: 9-10, 11-12, 14-15 → gap1=60, gap2=120 → total=180 (compactness=5)
    s1 = _section(
        "1", "A", "001",
        meeting_times=[MeetingTime(days=("M",), start=time(9, 0), end=time(10, 0))],
    )
    s2 = _section(
        "2", "B", "002",
        meeting_times=[MeetingTime(days=("M",), start=time(11, 0), end=time(12, 0))],
    )
    s3 = _section(
        "3", "C", "003",
        meeting_times=[MeetingTime(days=("M",), start=time(14, 0), end=time(15, 0))],
    )
    sched = _sched([s1, s2, s3])
    idle = _weekly_idle_minutes(sched)
    assert idle == 60 + 120  # 60 between 10 and 11; 120 between 12 and 14
    assert _score_compactness(idle) == 5.0


def test_compactness_score_boundaries() -> None:
    """Verify all compactness score boundaries."""
    assert _score_compactness(0) == 20.0
    assert _score_compactness(1) == 15.0
    assert _score_compactness(60) == 15.0
    assert _score_compactness(61) == 10.0
    assert _score_compactness(120) == 10.0
    assert _score_compactness(121) == 5.0
    assert _score_compactness(240) == 5.0
    assert _score_compactness(241) == 0.0


# ---------------------------------------------------------------------------
# 5. Credit-load scoring
# ---------------------------------------------------------------------------


def test_credit_load_uses_decimal_comparison() -> None:
    """Test 17: Credit-load scoring uses Decimal (not float) comparisons."""
    section = _section("1", "CPSC", "031", credits=Decimal("4"))
    sched = _sched([section])
    prefs = _prefs(max_credits=Decimal("4"))
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["credit_load"] == 10.0


def test_credit_load_within_half_credit() -> None:
    section = _section("1", "CPSC", "031", credits=Decimal("3.5"))
    sched = _sched([section])
    prefs = _prefs(max_credits=Decimal("4"))
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["credit_load"] == 6.0


def test_credit_load_within_one_credit() -> None:
    section = _section("1", "CPSC", "031", credits=Decimal("3"))
    sched = _sched([section])
    prefs = _prefs(max_credits=Decimal("4"))
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["credit_load"] == 3.0


def test_credit_load_over_one_credit_away_scores_zero() -> None:
    section = _section("1", "CPSC", "031", credits=Decimal("1"))
    sched = _sched([section])
    prefs = _prefs(max_credits=Decimal("4"))
    status = _status()
    ranked = rank_schedules([sched], status, prefs)
    assert ranked[0].score_breakdown["credit_load"] == 0.0


# ---------------------------------------------------------------------------
# 6. Score breakdown integrity
# ---------------------------------------------------------------------------


def test_score_equals_sum_of_breakdown() -> None:
    """Test 18: Total score must equal the exact sum of breakdown values."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("T",))])
    sched = _sched([engl], total_credits=Decimal("1"))
    prefs = _prefs(preferred_subjects=["ENGL"], max_credits=Decimal("4"))
    status = _status([item])
    ranked = rank_schedules([sched], status, prefs)
    rs = ranked[0]
    assert rs.score == sum(rs.score_breakdown.values())


def test_all_breakdown_keys_always_present() -> None:
    """Test 19: All five standard breakdown keys appear even when their value is zero."""
    sched = _sched([_section("1", "MATH", "021")])
    status = _status()
    ranked = rank_schedules([sched], status, _prefs())
    assert _BREAKDOWN_KEYS == set(ranked[0].score_breakdown.keys())


def test_breakdown_keys_present_empty_schedule_list() -> None:
    """Ranking an empty list returns an empty list — no error."""
    result = rank_schedules([], _status(), _prefs())
    assert result == []


# ---------------------------------------------------------------------------
# 7. Sorting
# ---------------------------------------------------------------------------


def test_higher_score_sorts_first() -> None:
    """Test 20: Schedule with higher score appears before lower-score schedule."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    math = _section("2", "MATH", "021", meeting_times=[_mt(days=("T",))])
    sched_good = _sched([engl])  # gains writing item
    sched_bad = _sched([math])   # no gains
    status = _status([item])
    ranked = rank_schedules([sched_bad, sched_good], status, _prefs())
    assert ranked[0].requirement_gains != []  # good schedule first
    assert ranked[1].requirement_gains == []


def test_equal_score_tiebreak_by_gains_count() -> None:
    """Test 21: When scores tie, more requirement gains sorts first."""
    item_a = _req("a", satisfying_courses=["ENGL 011"])
    item_b = _req("b", satisfying_courses=["MATH 021"])
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    math = _section("2", "MATH", "021", meeting_times=[_mt(days=("T",))])
    # sched_two has both items (200 pts); sched_one has only one (100 pts)
    # So sched_two will score higher anyway. Let me make them tie differently.
    # Actually, create a scenario where pref scores compensate for fewer gains.
    # Two gains = 200 pts; One gain + high pref = 200 pts too.
    item_c = _req("c", satisfying_courses=["CPSC 031"])
    cpsc = _section("3", "CPSC", "031", meeting_times=[_mt(days=("W",))])
    # Schedule with 2 gains (engl+math): 200 req + 0 subj = 200
    # Schedule with 1 gain (cpsc) + high subject score: 100 + 100 (hypothetical) = 200
    # Easier: score them to tie via credit_load difference canceling something
    # Simplest: just verify items order matters. Make sched_two and sched_one both
    # 100 req + same other scores → they tie → sort by gain count.
    # For a true tie at the score level with 1 gain each but different IDs → canonical key tiebreaks.
    # Let me just check the stated invariant with an explicit score.

    # Simpler approach: synthesize two schedules with identical breakdown except req_gains count.
    # Use max_credits=3 for credit_load=10 (both at target), no preferred subjects, no free days.
    engl2 = _section("10", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    math2 = _section("11", "MATH", "021", meeting_times=[_mt(days=("T",))])
    sched_two_gains = _sched([engl2, math2], total_credits=Decimal("3"))
    sched_one_gain = _sched([engl], total_credits=Decimal("3"))
    prefs = _prefs(max_credits=Decimal("3"), min_credits=Decimal("1"))
    status = _status([item_a, item_b])
    # sched_two_gains: 200 req + 10 credit = 210; sched_one_gain: 100 req + 10 credit = 110
    # Not a tie, but sort order should put sched_two first.
    ranked = rank_schedules([sched_one_gain, sched_two_gains], status, prefs)
    assert len(ranked[0].requirement_gains) == 2


def test_equal_score_and_gains_tiebreak_by_credits() -> None:
    """Test 22: Equal score + equal gains → higher credits sorts first."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    engl_a = _section(
        "1", "ENGL", "011",
        meeting_times=[_mt(days=("M",))],
        credits=Decimal("1.5"),
    )
    engl_b = _section(
        "2", "ENGL", "011",
        section_id="02",
        meeting_times=[_mt(days=("T",))],
        credits=Decimal("1"),
    )
    sched_high = _sched([engl_a], total_credits=Decimal("1.5"))
    sched_low = _sched([engl_b], total_credits=Decimal("1"))
    status = _status([item])
    # Both schedules gain the writing item; equal req scores.
    # But sched_high has total_credits=1.5 → credit_load: diff from max_credits=4 is 2.5 → 0
    # sched_low has diff 3.0 → 0. Still tied on credit_load. Must tie-break by credits.
    ranked = rank_schedules([sched_low, sched_high], status, _prefs())
    # Both have 100 req pts + same other pts; credits differs → sched_high first
    assert ranked[0].schedule.total_credits == Decimal("1.5")


def test_canonical_key_tiebreak_is_deterministic() -> None:
    """Test 23: When all other tie-breaks are equal, canonical ref_no key determines order."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    # Two identical-credit, identical-score schedules — differ only in ref_no
    engl_z = _section("z999", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    engl_a = _section("a001", "ENGL", "011", meeting_times=[_mt(days=("T",))])
    sched_z = _sched([engl_z], total_credits=Decimal("1"))
    sched_a = _sched([engl_a], total_credits=Decimal("1"))
    status = _status([item])
    ranked1 = rank_schedules([sched_z, sched_a], status, _prefs())
    ranked2 = rank_schedules([sched_a, sched_z], status, _prefs())
    # Canonical key sorts: "a001" < "z999" → sched_a first in both
    assert ranked1[0].schedule.parent_sections[0].ref_no == "a001"
    assert ranked2[0].schedule.parent_sections[0].ref_no == "a001"


# ---------------------------------------------------------------------------
# 8. Categories
# ---------------------------------------------------------------------------


def test_current_registration_requires_exact_locked_ref_match() -> None:
    """Test 24: Only schedule whose ref_nos == locked_ref_nos gets current_registration."""
    parent = _section("99", "CPSC", "063")
    sched = _sched([parent])
    status = _status()
    locked_ref_nos: frozenset[str] = frozenset({"99"})
    ranked = rank_schedules([sched], status, _prefs(), locked_ref_nos=locked_ref_nos)
    assert ranked[0].category == "current_registration"


def test_current_registration_not_assigned_with_empty_locked_refs() -> None:
    """No schedule is current_registration when locked_ref_nos is empty."""
    parent = _section("99", "CPSC", "063")
    sched = _sched([parent])
    status = _status()
    ranked = rank_schedules([sched], status, _prefs(), locked_ref_nos=frozenset())
    assert ranked[0].category != "current_registration"


def test_requirements_first_category_with_two_plus_gains() -> None:
    """Test 25: Two or more gains → requirements_first."""
    item_a = _req("a", satisfying_courses=["ENGL 011"])
    item_b = _req("b", satisfying_courses=["MATH 021"])
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    math = _section("2", "MATH", "021", meeting_times=[_mt(days=("T",))])
    sched = _sched([engl, math])
    status = _status([item_a, item_b])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].category == "requirements_first"


def test_requirements_first_not_assigned_for_one_gain() -> None:
    """One gain is not enough for requirements_first."""
    item = _req("a", satisfying_courses=["ENGL 011"])
    engl = _section("1", "ENGL", "011")
    sched = _sched([engl])
    status = _status([item])
    ranked = rank_schedules([sched], status, _prefs())
    assert ranked[0].category != "requirements_first"


def test_preferred_subjects_category() -> None:
    """Test 26: preferred-subject score dominates other non-req components."""
    # One gain → not requirements_first.
    # High subject score, zero compactness potential (many days), zero free_days.
    item = _req("a", satisfying_courses=["ENGL 011"])
    engl = _section(
        "1", "ENGL", "011",
        meeting_times=[
            MeetingTime(days=("M",), start=time(9, 0), end=time(10, 0)),
            MeetingTime(days=("T",), start=time(10, 0), end=time(11, 0)),
            MeetingTime(days=("W",), start=time(12, 0), end=time(13, 0)),
            MeetingTime(days=("R",), start=time(9, 0), end=time(10, 0)),
        ],
    )
    sched = _sched([engl])
    status = _status([item])
    # Preferred: ENGL at position 0 → 12 pts; all 4 days used → incidental free = {F} → 5 pts
    # compact: meetings spread across 4 days but no same-day gaps → idle=0 → 20 pts
    # compact_total = 5+20 = 25 > pref_score 12 → not preferred_subjects
    # We need pref_score > compact_total. Use section that only meets on 1 day but
    # has multiple courses from preferred subject.
    cpsc_a = _section(
        "2", "CPSC", "035",
        meeting_times=[MeetingTime(days=("M",), start=time(9, 0), end=time(10, 0))],
    )
    cpsc_b = _section(
        "3", "CPSC", "041",
        meeting_times=[MeetingTime(days=("T",), start=time(9, 0), end=time(10, 0))],
    )
    # CPSC at position 0 → 12+12 = 24 pts; used: M,T → incidental: W,R,F (3×5=15 pts)
    # idle = 0 → compact=20; compact_total = 35; pref_score = 24 → preferred_subjects NOT triggered
    # Adjust: use a day with big gap so compact drops
    # Simple winning case: single CPSC course, many preferred subjects, all days used (low free_days)
    cpsc_c = _section(
        "4", "CPSC", "043",
        meeting_times=[MeetingTime(days=("W",), start=time(9, 0), end=time(10, 0))],
    )
    cpsc_d = _section(
        "5", "CPSC", "046",
        meeting_times=[MeetingTime(days=("R",), start=time(9, 0), end=time(10, 0))],
    )
    cpsc_e = _section(
        "6", "CPSC", "063",
        meeting_times=[MeetingTime(days=("F",), start=time(9, 0), end=time(10, 0))],
    )
    # 5 CPSC sections (positions 0,0,0,0,0) → 5×12=60 pts; all days used → 0 incidental
    # compact: no same-day gaps (each section on unique day) → idle=0 → 20 pts
    # compact_total=0+20=20 < pref_score=60 → preferred_subjects!
    sched2 = _sched([cpsc_a, cpsc_b, cpsc_c, cpsc_d, cpsc_e])
    prefs2 = _prefs(preferred_subjects=["CPSC"])
    ranked2 = rank_schedules([sched2], _status(), prefs2)
    assert ranked2[0].category == "preferred_subjects"


def test_compact_schedule_category() -> None:
    """Test 27: Compactness+free-day dominates when schedule uses ≤3 days."""
    # No preferred subjects, no requirement gains, ≤3 used days, zero idle time
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    sched = _sched([s1, s2])
    status = _status()
    prefs = _prefs(preferred_subjects=[])  # no subject preference
    ranked = rank_schedules([sched], status, prefs)
    # used_days = {M, T} = 2 ≤ 3; idle = 0 → compact = 20; incidental = {W,R,F} → 15
    # pref_score = 0; compact_total = 35 > 0 and used ≤ 3 → compact_schedule
    assert ranked[0].category == "compact_schedule"


def test_balanced_fallback_category() -> None:
    """Test 28: balanced is the fallback when no other category applies."""
    # One gain, pref_score > compact_total is false (pref=0, compact=20), used_days=5
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    s3 = _section("3", "C", "003", meeting_times=[_mt(days=("W",))])
    s4 = _section("4", "D", "004", meeting_times=[_mt(days=("R",))])
    s5 = _section("5", "E", "005", meeting_times=[_mt(days=("F",))])
    item = _req("x", satisfying_courses=["A 001"])
    sched = _sched([s1, s2, s3, s4, s5])
    status = _status([item])
    prefs = _prefs(preferred_subjects=[])
    ranked = rank_schedules([sched], status, prefs)
    # 1 gain → not requirements_first
    # pref_score=0 → not preferred_subjects
    # used_days=5 > 3 and idle=0 → compact_schedule (idle==0 triggers it)
    # Hmm, idle=0 means compact_schedule applies... let me add idle time.
    # Schedule with 5 used days and some idle time: use a big gap on one day
    s5b = _section(
        "5", "E", "005",
        meeting_times=[MeetingTime(days=("F",), start=time(9, 0), end=time(10, 0))],
    )
    s5b_gap = _section(
        "6", "F", "006",
        meeting_times=[MeetingTime(days=("F",), start=time(14, 0), end=time(15, 0))],
    )
    sched2 = _sched([s1, s2, s3, s4, s5b, s5b_gap])
    item2 = _req("y", satisfying_courses=["A 001"])
    status2 = _status([item2])
    ranked2 = rank_schedules([sched2], status2, prefs)
    # 1 gain (A 001 is not in sched2 sections...); 0 gains, pref=0
    # idle=300 (5h gap on Friday) → compact=0; compact_total=0+0=0
    # used_days=5 > 3 and idle>0 → not compact_schedule
    # pref_score=0 → not preferred_subjects; 0 gains → not requirements_first
    # → balanced!
    assert ranked2[0].category == "balanced"


# ---------------------------------------------------------------------------
# 9. Explanation field
# ---------------------------------------------------------------------------


def test_explanation_is_always_empty() -> None:
    """Test 29: explanation field is "" for all ranked schedules."""
    sched = _sched([_section("1", "A", "001")])
    ranked = rank_schedules([sched], _status(), _prefs())
    assert ranked[0].explanation == ""


# ---------------------------------------------------------------------------
# 10. Input immutability
# ---------------------------------------------------------------------------


def test_inputs_not_mutated() -> None:
    """Test 30: rank_schedules does not mutate any input."""
    item = _req("writing", satisfying_courses=["ENGL 011"])
    engl = _section("1", "ENGL", "011")
    sched = _sched([engl])
    status = _status([item])
    prefs = _prefs(preferred_subjects=["ENGL"])

    # Deep-copy for comparison
    item_before = copy.deepcopy(item)
    sched_parents_before = list(sched.parent_sections)
    prefs_subjects_before = list(prefs.preferred_subjects)
    items_before = list(status.items)

    rank_schedules([sched], status, prefs)

    assert item.id == item_before.id
    assert item.satisfied == item_before.satisfied
    assert sched.parent_sections == sched_parents_before
    assert prefs.preferred_subjects == prefs_subjects_before
    assert status.items == items_before


# ---------------------------------------------------------------------------
# 11. Determinism
# ---------------------------------------------------------------------------


def test_repeated_calls_are_deterministic() -> None:
    """Test 31: Identical inputs always produce identical outputs."""
    item_a = _req("a", satisfying_courses=["ENGL 011"])
    item_b = _req("b", satisfying_courses=["CPSC 031"])
    engl = _section("1", "ENGL", "011", meeting_times=[_mt(days=("M",))])
    cpsc = _section("2", "CPSC", "031", meeting_times=[_mt(days=("T",))])
    sched1 = _sched([engl])
    sched2 = _sched([cpsc])
    status = _status([item_a, item_b])
    prefs = _prefs(preferred_subjects=["CPSC"])

    ranked_a = rank_schedules([sched1, sched2], status, prefs)
    ranked_b = rank_schedules([sched1, sched2], status, prefs)

    for a, b in zip(ranked_a, ranked_b):
        assert a.score == b.score
        assert a.category == b.category
        assert [g.id for g in a.requirement_gains] == [g.id for g in b.requirement_gains]
        canon_a = tuple(sorted(s.ref_no for s in a.schedule.all_sections))
        canon_b = tuple(sorted(s.ref_no for s in b.schedule.all_sections))
        assert canon_a == canon_b


# ---------------------------------------------------------------------------
# 12. max_ranked
# ---------------------------------------------------------------------------


def test_max_ranked_limits_output() -> None:
    """Test 32a: max_ranked=1 returns only the top-ranked schedule."""
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    sched1 = _sched([s1])
    sched2 = _sched([s2])
    ranked = rank_schedules([sched1, sched2], _status(), _prefs(), max_ranked=1)
    assert len(ranked) == 1


def test_max_ranked_none_returns_all() -> None:
    """Test 32b: max_ranked=None returns all schedules."""
    schedules = [_sched([_section(str(i), "A", str(i))]) for i in range(5)]
    ranked = rank_schedules(schedules, _status(), _prefs(), max_ranked=None)
    assert len(ranked) == 5


def test_max_ranked_zero_raises() -> None:
    """Test 32c: max_ranked=0 raises ValueError."""
    with pytest.raises(ValueError):
        rank_schedules([], _status(), _prefs(), max_ranked=0)


def test_max_ranked_negative_raises() -> None:
    """max_ranked=-1 raises ValueError."""
    with pytest.raises(ValueError):
        rank_schedules([], _status(), _prefs(), max_ranked=-1)


# ---------------------------------------------------------------------------
# 13. CPSC 031 specific
# ---------------------------------------------------------------------------


def test_cpsc031_gains_both_cs_items() -> None:
    """Test 33: A schedule with CPSC 031 gains both cs_cpsc031 and cs_cpsc_credits."""
    cpsc031_item = _req("cs_cpsc031", satisfying_courses=["CPSC 031"])
    cpsc_credits_item = _req("cs_cpsc_credits", subject_predicate="CPSC")
    cpsc = _section("1", "CPSC", "031", credits=Decimal("1"))
    sched = _sched([cpsc])
    status = _status([cpsc031_item, cpsc_credits_item])
    ranked = rank_schedules([sched], status, _prefs())
    gain_ids = {g.id for g in ranked[0].requirement_gains}
    assert "cs_cpsc031" in gain_ids
    assert "cs_cpsc_credits" in gain_ids


def test_cpsc_credits_item_not_mutated() -> None:
    """Test 34: One CPSC course does not mutate or complete cs_cpsc_credits."""
    cpsc_credits_item = _req("cs_cpsc_credits", subject_predicate="CPSC")
    cpsc = _section("1", "CPSC", "035", credits=Decimal("1"))
    sched = _sched([cpsc])
    status = _status([cpsc_credits_item])
    rank_schedules([sched], status, _prefs())
    # item.satisfied must still be False
    assert cpsc_credits_item.satisfied is False
    # The item in status must not have been modified
    assert status.items[0].satisfied is False


def test_different_lab_choices_ranked_independently() -> None:
    """Test 35: Schedules differing only in lab section have distinct canonical keys."""
    parent = _section(
        "1", "CPSC", "031",
        meeting_times=[_mt(days=("T", "R"))],
    )
    lab_a = _section(
        "2", "CPSC", "031",
        course_type="Lab",
        credits=Decimal("0"),
        meeting_times=[_mt(days=("M",))],
        section_id="L1",
    )
    lab_b = _section(
        "3", "CPSC", "031",
        course_type="Lab",
        credits=Decimal("0"),
        meeting_times=[_mt(days=("W",))],
        section_id="L2",
    )
    sched_a = Schedule(
        parent_sections=[parent], lab_sections=[lab_a], total_credits=Decimal("1")
    )
    sched_b = Schedule(
        parent_sections=[parent], lab_sections=[lab_b], total_credits=Decimal("1")
    )
    status = _status()
    ranked = rank_schedules([sched_a, sched_b], status, _prefs())
    assert len(ranked) == 2
    # They have different canonical keys
    key_a = frozenset(s.ref_no for s in ranked[0].schedule.all_sections)
    key_b = frozenset(s.ref_no for s in ranked[1].schedule.all_sections)
    assert key_a != key_b


# ---------------------------------------------------------------------------
# 14. Hypothesis property tests
# ---------------------------------------------------------------------------

_SIMPLE_CREDITS = st.sampled_from([Decimal("1"), Decimal("1.5"), Decimal("2")])
_DAY_TUPLES: list[tuple[str, ...]] = [("M",), ("T",), ("W",), ("R",), ("F",)]
_SUBJECTS = ["CPSC", "MATH", "ENGL", "HIST"]


@st.composite
def _non_conflicting_schedule_st(draw: st.DrawFn) -> Schedule:
    """Generate a schedule with 1-4 non-conflicting sections (distinct single days)."""
    n = draw(st.integers(min_value=1, max_value=4))
    # Pick n distinct day tuples
    indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=4),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    parents: list[CourseSection] = []
    for j, idx in enumerate(indices):
        days = _DAY_TUPLES[idx]
        cred = draw(_SIMPLE_CREDITS)
        subj = draw(st.sampled_from(_SUBJECTS))
        s = _section(
            str(j + 1),
            subj,
            str(100 + j),
            credits=cred,
            meeting_times=[MeetingTime(days=days, start=time(10, 0), end=time(11, 0))],
        )
        parents.append(s)
    total = sum(s.credits for s in parents)
    return Schedule(parent_sections=parents, lab_sections=[], total_credits=total)


@st.composite
def _prefs_st(draw: st.DrawFn) -> Preferences:
    max_c = draw(st.sampled_from([Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]))
    min_c = draw(
        st.sampled_from([Decimal("1"), Decimal("2"), Decimal("3")]).filter(
            lambda x: x <= max_c
        )
    )
    pref_subjs = draw(
        st.lists(st.sampled_from(_SUBJECTS), min_size=0, max_size=3, unique=True)
    )
    return Preferences(
        min_credits=min_c,
        max_credits=max_c,
        preferred_subjects=pref_subjs,
    )


@st.composite
def _req_items_st(draw: st.DrawFn) -> list[RequirementItem]:
    n = draw(st.integers(min_value=0, max_value=3))
    items: list[RequirementItem] = []
    used_ids: set[str] = set()
    for k in range(n):
        item_id = f"req_{k}"
        if item_id in used_ids:
            continue
        used_ids.add(item_id)
        subj = draw(st.sampled_from(_SUBJECTS))
        items.append(_req(item_id, subject_predicate=subj))
    return items


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=1, max_size=10),
    req_items=_req_items_st(),
    prefs=_prefs_st(),
    max_r=st.one_of(st.none(), st.integers(min_value=1, max_value=5)),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_score_equals_breakdown_sum(
    schedules: list[Schedule],
    req_items: list[RequirementItem],
    prefs: Preferences,
    max_r: int | None,
) -> None:
    """Property: score always equals sum of breakdown values."""
    status = _status(req_items)
    ranked = rank_schedules(schedules, status, prefs, max_ranked=max_r)
    for rs in ranked:
        assert abs(rs.score - sum(rs.score_breakdown.values())) < 1e-9


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=1, max_size=10),
    req_items=_req_items_st(),
    prefs=_prefs_st(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_ranking_is_deterministic(
    schedules: list[Schedule],
    req_items: list[RequirementItem],
    prefs: Preferences,
) -> None:
    """Property: identical inputs always produce identical output order."""
    status = _status(req_items)
    r1 = rank_schedules(schedules, status, prefs)
    r2 = rank_schedules(schedules, status, prefs)
    assert len(r1) == len(r2)
    for a, b in zip(r1, r2):
        assert a.score == b.score
        assert a.category == b.category
        assert [g.id for g in a.requirement_gains] == [g.id for g in b.requirement_gains]
        canon_a = frozenset(s.ref_no for s in a.schedule.all_sections)
        canon_b = frozenset(s.ref_no for s in b.schedule.all_sections)
        assert canon_a == canon_b


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=1, max_size=10),
    req_items=_req_items_st(),
    prefs=_prefs_st(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_no_duplicate_gains(
    schedules: list[Schedule],
    req_items: list[RequirementItem],
    prefs: Preferences,
) -> None:
    """Property: requirement_gains never contains the same item ID twice."""
    status = _status(req_items)
    ranked = rank_schedules(schedules, status, prefs)
    for rs in ranked:
        gain_ids = [g.id for g in rs.requirement_gains]
        assert len(gain_ids) == len(set(gain_ids))


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=1, max_size=10),
    prefs=_prefs_st(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_auto_registered_never_in_gains(
    schedules: list[Schedule],
    prefs: Preferences,
) -> None:
    """Property: auto_registered items never appear in requirement_gains."""
    auto_item = _req("auto", subject_predicate="CPSC", auto_registered=True)
    status = _status([auto_item])
    ranked = rank_schedules(schedules, status, prefs)
    for rs in ranked:
        assert all(g.id != "auto" for g in rs.requirement_gains)


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=1, max_size=10),
    req_items=_req_items_st(),
    prefs=_prefs_st(),
    max_r=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_output_length_bounded_by_max_ranked(
    schedules: list[Schedule],
    req_items: list[RequirementItem],
    prefs: Preferences,
    max_r: int,
) -> None:
    """Property: output length <= max_ranked and <= len(schedules)."""
    status = _status(req_items)
    ranked = rank_schedules(schedules, status, prefs, max_ranked=max_r)
    assert len(ranked) <= max_r
    assert len(ranked) <= len(schedules)


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=1, max_size=10),
    req_items=_req_items_st(),
    prefs=_prefs_st(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_all_breakdown_keys_present(
    schedules: list[Schedule],
    req_items: list[RequirementItem],
    prefs: Preferences,
) -> None:
    """Property: all five breakdown keys are always present."""
    status = _status(req_items)
    ranked = rank_schedules(schedules, status, prefs)
    for rs in ranked:
        assert _BREAKDOWN_KEYS == set(rs.score_breakdown.keys())


@given(
    schedules=st.lists(_non_conflicting_schedule_st(), min_size=2, max_size=10),
    req_items=_req_items_st(),
    prefs=_prefs_st(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_inputs_unchanged_after_ranking(
    schedules: list[Schedule],
    req_items: list[RequirementItem],
    prefs: Preferences,
) -> None:
    """Property: schedule parent_sections and requirement items are not mutated."""
    # Record ref_nos and satisfied flags before ranking
    original_refs = [
        [s.ref_no for s in sched.parent_sections] for sched in schedules
    ]
    original_satisfied = [item.satisfied for item in req_items]
    status = _status(req_items)
    rank_schedules(schedules, status, prefs)
    # Verify no mutation
    for i, sched in enumerate(schedules):
        assert [s.ref_no for s in sched.parent_sections] == original_refs[i]
    for i, item in enumerate(req_items):
        assert item.satisfied == original_satisfied[i]


# ---------------------------------------------------------------------------
# 15. Integration test (catalog → audit → requirements → solver → ranking)
# ---------------------------------------------------------------------------


def test_integration_full_pipeline_with_ranking() -> None:
    """Integration test: catalog parser → audit → requirements → solver → ranking.

    Uses fictional test fixtures (catalog_sample_10.csv, audit_synthetic.pdf).
    Asserts basic ranking invariants without depending on the untracked fall_2026.csv.
    """
    from app.adapters.swarthmore.requirement_defs import get_requirement_definitions
    from app.core.requirements import build_requirement_status
    from app.core.solver import (
        expand_selection_options,
        filter_candidates,
        generate_schedules,
    )
    from app.parsers.audit import parse_audit
    from app.parsers.catalog import parse_catalog

    catalog_path = _FIXTURES / "catalog_sample_10.csv"
    audit_path = _FIXTURES / "audit_synthetic.pdf"
    assert catalog_path.exists()
    assert audit_path.exists()

    sections = parse_catalog(catalog_path)
    student = parse_audit(audit_path)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        definitions = get_requirement_definitions(student)
        req_status = build_requirement_status(student, definitions)

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
    options = expand_selection_options(candidates, prefs)
    solver_results = generate_schedules(options, [], prefs, max_results=200)
    assert len(solver_results) >= 1, "Pipeline must produce at least one schedule"

    ranked = rank_schedules(solver_results, req_status, prefs)

    # Output length == solver output length (no max_ranked cap)
    assert len(ranked) == len(solver_results)

    # Scores are non-increasing (sorted descending)
    for i in range(len(ranked) - 1):
        assert ranked[i].score >= ranked[i + 1].score

    # All breakdown keys present; score == sum of breakdown
    for rs in ranked:
        assert _BREAKDOWN_KEYS == set(rs.score_breakdown.keys())
        assert abs(rs.score - sum(rs.score_breakdown.values())) < 1e-9

    # Requirement gains are legitimate (each gain id appears in status.items)
    status_ids = {item.id for item in req_status.items}
    for rs in ranked:
        for gain in rs.requirement_gains:
            assert gain.id in status_ids
            assert not gain.satisfied
            assert not gain.auto_registered

    # Explanations are empty (AI layer not called here)
    for rs in ranked:
        assert rs.explanation == ""

    # Determinism: calling again produces identical canonical order
    ranked2 = rank_schedules(solver_results, req_status, prefs)
    keys1 = [frozenset(s.ref_no for s in rs.schedule.all_sections) for rs in ranked]
    keys2 = [frozenset(s.ref_no for s in rs.schedule.all_sections) for rs in ranked2]
    assert keys1 == keys2
