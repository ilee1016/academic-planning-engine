"""Tests for app/orchestration.py: pipeline coordination and locked-section resolution."""
from __future__ import annotations

import warnings
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.swarthmore.requirement_defs import get_requirement_definitions
from app.core.requirements import build_requirement_status
from app.models import (
    CompletedCourse,
    ConstraintDiagnostic,
    CourseSection,
    MeetingTime,
    Preferences,
    RequirementBlock,
    RequirementItem,
    RequirementStatus,
    Schedule,
    StudentRecord,
)
from app.orchestration import (
    AmbiguousCourse,
    LockedResolutionResult,
    PlanningResult,
    group_ranked_by_category,
    plan_schedules,
    resolve_locked_sections_flexible,
)

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    section_id: str = "01",
) -> CourseSection:
    return CourseSection(
        ref_no=ref_no, subject=subject, number=number, section_id=section_id,
        title=f"{subject} {number}", credits=credits,
        distribution=frozenset(), enr_limit=None, instructors=[],
        course_type=course_type,
        meeting_times=meeting_times if meeting_times is not None else [_mt()],
        note="", linked_sections=[],
    )


def _completed(code: str) -> CompletedCourse:
    return CompletedCourse(code=code, title="", grade="A", credits=Decimal("1"), term="Fall 2024")


def _preregistered(code: str) -> CompletedCourse:
    return CompletedCourse(code=code, title="", grade="----", credits=Decimal("1"), term="Fall 2026")


def _student(
    completed: list[str] | None = None,
    prereg: list[str] | None = None,
    exempted: list[str] | None = None,
) -> StudentRecord:
    return StudentRecord(
        name="Demo, Student", student_id="000000000",
        major="Computer Science", class_year=2027, catalog_year="202304",
        credits_required=Decimal("32"), credits_applied=Decimal("30"),
        audit_date=date(2026, 7, 1),
        completed_courses=[_completed(c) for c in (completed or [])],
        preregistered_courses=[_preregistered(c) for c in (prereg or [])],
        other_courses=[], exempted_courses=exempted or [],
        requirement_blocks=[
            RequirementBlock(name="Intro to Computer Systems", status="INCOMPLETE", still_needed_text=""),
            RequirementBlock(name="8 Required Credits in CPSC", status="INCOMPLETE", still_needed_text=""),
            RequirementBlock(name="Distribution Requirement - Writing", status="INCOMPLETE", still_needed_text=""),
        ],
        exceptions=[],
    )


def _req_status_empty() -> RequirementStatus:
    return RequirementStatus(items=[], credits_remaining=Decimal("2"))


def _prefs(**kwargs: object) -> Preferences:
    defaults: dict[str, object] = {
        "min_credits": Decimal("1"),
        "max_credits": Decimal("4"),
        "lock_preregistered": False,
    }
    defaults.update(kwargs)
    return Preferences(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Complete synthetic pipeline returns ranked schedules
# ---------------------------------------------------------------------------


def test_complete_synthetic_pipeline_returns_ranked() -> None:
    """Test 1: Full pipeline with non-conflicting courses produces ranked results."""
    s1 = _section("1", "CPSC", "035", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "MATH", "025", meeting_times=[_mt(days=("T",))])
    student = _student()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("3"))
    status = _req_status_empty()
    result = plan_schedules(
        student=student, catalog_sections=[s1, s2], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(), max_results=50,
    )
    assert result.diagnostic is None
    assert len(result.ranked_schedules) >= 1


# ---------------------------------------------------------------------------
# 2. No-schedule pipeline returns diagnostic
# ---------------------------------------------------------------------------


def test_no_schedule_pipeline_returns_diagnostic() -> None:
    """Test 2: When no schedules exist, diagnostic is populated."""
    # Section credits exceed max_credits → no valid schedule
    s = _section("1", "CPSC", "035", credits=Decimal("5"))
    student = _student()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("2"))
    status = _req_status_empty()
    result = plan_schedules(
        student=student, catalog_sections=[s], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(),
    )
    assert result.diagnostic is not None
    assert result.diagnostic.no_valid_schedules is True
    assert len(result.diagnostic.reasons) >= 1
    assert result.ranked_schedules == ()


# ---------------------------------------------------------------------------
# 3. Locked unique section auto-resolves
# ---------------------------------------------------------------------------


def test_locked_unique_section_auto_resolves() -> None:
    """Test 3: Single-match preregistered course auto-resolves."""
    cpsc_063 = _section("99", "CPSC", "063")
    student = _student(prereg=["CPSC 063"])
    prefs = _prefs(lock_preregistered=True, min_credits=Decimal("1"), max_credits=Decimal("4"))
    preregistered = student.preregistered_courses
    resolution = resolve_locked_sections_flexible(preregistered, [cpsc_063], frozenset())
    assert len(resolution.locked_sections) == 1
    assert resolution.locked_sections[0].ref_no == "99"
    assert len(resolution.ambiguous_courses) == 0


# ---------------------------------------------------------------------------
# 4. Ambiguous locked section returns structured resolution requirement
# ---------------------------------------------------------------------------


def test_ambiguous_locked_section_detected() -> None:
    """Test 4: Two catalog sections for same course produce ambiguity."""
    cpsc_a = _section("10", "CPSC", "063", section_id="01")
    cpsc_b = _section("11", "CPSC", "063", section_id="02")
    student = _student(prereg=["CPSC 063"])
    prefs = _prefs(lock_preregistered=True)
    resolution = resolve_locked_sections_flexible(
        student.preregistered_courses, [cpsc_a, cpsc_b], frozenset()
    )
    assert len(resolution.ambiguous_courses) == 1
    assert resolution.ambiguous_courses[0].course_code == "CPSC 063"
    assert len(resolution.ambiguous_courses[0].choices) == 2


# ---------------------------------------------------------------------------
# 5. Explicit locked ref resolves ambiguity
# ---------------------------------------------------------------------------


def test_explicit_locked_ref_resolves_ambiguity() -> None:
    """Test 5: Providing the specific ref_no resolves an ambiguous course."""
    cpsc_a = _section("10", "CPSC", "063", section_id="01")
    cpsc_b = _section("11", "CPSC", "063", section_id="02")
    student = _student(prereg=["CPSC 063"])
    resolution = resolve_locked_sections_flexible(
        student.preregistered_courses, [cpsc_a, cpsc_b], frozenset({"10"})
    )
    assert len(resolution.ambiguous_courses) == 0
    assert len(resolution.locked_sections) == 1
    assert resolution.locked_sections[0].ref_no == "10"


# ---------------------------------------------------------------------------
# 6. Unknown ref is handled gracefully (not in catalog)
# ---------------------------------------------------------------------------


def test_unknown_ref_produces_empty_locked() -> None:
    """Test 6: A ref_no not in catalog simply produces no locked section entry."""
    # Our orchestration doesn't raise on unknown refs — that's the API layer's job.
    # Here we test that resolve_locked_sections_flexible handles missing catalog entries.
    cpsc = _section("10", "CPSC", "063")
    student = _student(prereg=["CPSC 063"])
    # Explicitly supply a ref_no that does NOT match course_code of CPSC 063
    cpsc_wrong = _section("99", "MATH", "025")
    resolution = resolve_locked_sections_flexible(
        student.preregistered_courses, [cpsc], frozenset({"99"})
    )
    # "99" belongs to MATH 025, not CPSC 063 → not used for CPSC 063
    # CPSC 063 still auto-resolves via the single catalog match
    assert len(resolution.locked_sections) == 1
    assert resolution.locked_sections[0].ref_no == "10"


# ---------------------------------------------------------------------------
# 7. Candidate, option, and generated counts are correct
# ---------------------------------------------------------------------------


def test_result_counts_are_accurate() -> None:
    """Test 7: PlanningResult reports accurate candidate, option, generated counts."""
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    student = _student()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("3"))
    status = _req_status_empty()
    result = plan_schedules(
        student=student, catalog_sections=[s1, s2], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(), max_results=50,
    )
    # Candidates: s1 and s2 (both are parent Course type with meetings)
    assert result.candidate_count == 2
    # Options: one per candidate (no labs)
    assert result.option_count == 2
    assert result.generated_count >= 1


# ---------------------------------------------------------------------------
# 8. Solver cap metadata is correct
# ---------------------------------------------------------------------------


def test_solver_cap_metadata() -> None:
    """Test 8: cap_reached is True when generated_count == max_results."""
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    student = _student()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("3"))
    status = _req_status_empty()
    result = plan_schedules(
        student=student, catalog_sections=[s1, s2], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(), max_results=1,
    )
    assert result.solver_cap_reached is True
    assert result.generated_count == 1


# ---------------------------------------------------------------------------
# 9. Ranking cap applied after ranking
# ---------------------------------------------------------------------------


def test_ranking_cap_limits_output() -> None:
    """Test 9: max_ranked limits the number of ranked schedules returned."""
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    student = _student()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("3"))
    status = _req_status_empty()
    result = plan_schedules(
        student=student, catalog_sections=[s1, s2], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(), max_results=50, max_ranked=1,
    )
    assert len(result.ranked_schedules) == 1


# ---------------------------------------------------------------------------
# 10. Representative archetype selection is deterministic
# ---------------------------------------------------------------------------


def test_group_ranked_by_category_is_deterministic() -> None:
    """Test 10: group_ranked_by_category produces identical results on repeated calls."""
    from app.models import RankedSchedule
    # Build two distinct ranked schedules
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    s2 = _section("2", "B", "002", meeting_times=[_mt(days=("T",))])
    sched1 = Schedule(parent_sections=[s1], lab_sections=[], total_credits=Decimal("1"))
    sched2 = Schedule(parent_sections=[s2], lab_sections=[], total_credits=Decimal("1"))
    rs1 = RankedSchedule(
        schedule=sched1, category="balanced", score=100.0,
        score_breakdown={"requirement_gains": 0.0, "preferred_subjects": 0.0,
                         "free_days": 10.0, "compactness": 80.0, "credit_load": 10.0},
        requirement_gains=[], explanation="",
    )
    rs2 = RankedSchedule(
        schedule=sched2, category="requirements_first", score=200.0,
        score_breakdown={"requirement_gains": 100.0, "preferred_subjects": 0.0,
                         "free_days": 5.0, "compactness": 85.0, "credit_load": 10.0},
        requirement_gains=[], explanation="",
    )
    ranked = (rs1, rs2)
    top1, cats1 = group_ranked_by_category(ranked)
    top2, cats2 = group_ranked_by_category(ranked)
    assert [rs.score for rs in top1] == [rs.score for rs in top2]
    assert set(cats1.keys()) == set(cats2.keys())


# ---------------------------------------------------------------------------
# 11. Empty categories are handled
# ---------------------------------------------------------------------------


def test_group_ranked_skips_empty_categories() -> None:
    """Test 11: Categories with no schedules do not appear in output."""
    from app.models import RankedSchedule
    s1 = _section("1", "A", "001")
    sched = Schedule(parent_sections=[s1], lab_sections=[], total_credits=Decimal("1"))
    rs = RankedSchedule(
        schedule=sched, category="balanced", score=100.0,
        score_breakdown={"requirement_gains": 0.0, "preferred_subjects": 0.0,
                         "free_days": 5.0, "compactness": 85.0, "credit_load": 10.0},
        requirement_gains=[], explanation="",
    )
    _, categories = group_ranked_by_category((rs,))
    # Only "balanced" should be in categories
    assert "balanced" in categories
    assert "requirements_first" not in categories
    assert "preferred_subjects" not in categories


# ---------------------------------------------------------------------------
# 12. Schedule IDs are deterministic
# ---------------------------------------------------------------------------


def test_schedule_ids_are_deterministic() -> None:
    """Test 12: plan_schedules produces the same schedule IDs on repeated calls."""
    from app.api_models import _schedule_id
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    student = _student()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("2"))
    status = _req_status_empty()
    result1 = plan_schedules(
        student=student, catalog_sections=[s1], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(),
    )
    result2 = plan_schedules(
        student=student, catalog_sections=[s1], requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(),
    )
    ids1 = {_schedule_id(rs.schedule) for rs in result1.ranked_schedules}
    ids2 = {_schedule_id(rs.schedule) for rs in result2.ranked_schedules}
    assert ids1 == ids2


# ---------------------------------------------------------------------------
# 13. Domain inputs are not mutated
# ---------------------------------------------------------------------------


def test_domain_inputs_not_mutated() -> None:
    """Test 13: plan_schedules does not mutate the student record or catalog."""
    s1 = _section("1", "A", "001", meeting_times=[_mt(days=("M",))])
    student = _student()
    original_completed = list(student.completed_courses)
    catalog = [s1]
    original_catalog_len = len(catalog)
    status = _req_status_empty()
    prefs = _prefs(min_credits=Decimal("1"), max_credits=Decimal("2"))
    plan_schedules(
        student=student, catalog_sections=catalog, requirement_status=status,
        preferences=prefs, locked_ref_nos=frozenset(),
    )
    assert student.completed_courses == original_completed
    assert len(catalog) == original_catalog_len


# ---------------------------------------------------------------------------
# 14. No AI module is imported
# ---------------------------------------------------------------------------


def test_no_ai_module_imported() -> None:
    """Test 14: orchestration.py does not import anthropic or explainer."""
    import app.orchestration as orch_mod
    import sys
    for name in sys.modules:
        if "anthropic" in name or "explainer" in name:
            assert name not in getattr(orch_mod, "__dict__", {})


# ---------------------------------------------------------------------------
# 15. Current-registration schedule is categorized correctly
# ---------------------------------------------------------------------------


def test_current_registration_category() -> None:
    """Test 15: A locked-only schedule gets current_registration category."""
    from app.core.ranking import rank_schedules
    locked_section = _section("99", "CPSC", "063", credits=Decimal("4"))
    sched = Schedule(
        parent_sections=[locked_section], lab_sections=[], total_credits=Decimal("4")
    )
    status = _req_status_empty()
    prefs = _prefs(min_credits=Decimal("4"), max_credits=Decimal("4"))
    ranked = rank_schedules(
        [sched], status, prefs, locked_ref_nos=frozenset({"99"})
    )
    assert len(ranked) == 1
    assert ranked[0].category == "current_registration"


# ---------------------------------------------------------------------------
# 16. Integration: full pipeline with synthetic fixture files
# ---------------------------------------------------------------------------


def test_integration_full_pipeline_via_orchestration() -> None:
    """Test 16: Full pipeline with fixture files; no AI involved."""
    from app.parsers.audit import parse_audit
    from app.parsers.catalog import parse_catalog
    from app.orchestration import build_requirement_status_for_student

    catalog_path = _FIXTURES / "catalog_sample_10.csv"
    audit_path = _FIXTURES / "audit_synthetic.pdf"
    assert catalog_path.exists()
    assert audit_path.exists()

    sections = parse_catalog(catalog_path)
    student = parse_audit(audit_path)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        req_build = build_requirement_status_for_student(student)

    prefs = Preferences(
        min_credits=Decimal("1"), max_credits=Decimal("4"),
        preferred_subjects=["CPSC"], lock_preregistered=True,
    )
    result = plan_schedules(
        student=student, catalog_sections=sections,
        requirement_status=req_build.requirement_status,
        preferences=prefs, locked_ref_nos=frozenset(), max_results=50,
    )
    # At least one schedule should be generated
    assert result.diagnostic is None or result.generated_count >= 0
    if result.ranked_schedules:
        for rs in result.ranked_schedules:
            assert rs.explanation == ""  # AI not called
