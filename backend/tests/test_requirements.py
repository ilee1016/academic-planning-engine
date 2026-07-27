"""Tests for core/requirements.py.

Unit tests use purely synthetic domain objects (no file I/O).
One integration test exercises the full pipeline against fixture files.
"""
from __future__ import annotations

import warnings
from copy import deepcopy
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.swarthmore.requirement_defs import get_requirement_definitions
from app.core.requirements import (
    RequirementEvaluationError,
    build_requirement_status,
)
from app.models import (
    CourseSection,
    MeetingTime,
    RequirementBlock,
    RequirementDefinition,
    RequirementItem,
    StudentRecord,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Synthetic helpers
# ---------------------------------------------------------------------------


def _make_student(
    *,
    major: str = "Computer Science",
    catalog_year: str = "202304",
    credits_required: str = "32",
    credits_applied: str = "29",
    requirement_blocks: list[RequirementBlock] | None = None,
) -> StudentRecord:
    return StudentRecord(
        name="Student, Demo",
        student_id="000000000",
        major=major,
        class_year=2025,
        catalog_year=catalog_year,
        credits_required=Decimal(credits_required),
        credits_applied=Decimal(credits_applied),
        audit_date=date(2026, 1, 1),
        completed_courses=[],
        preregistered_courses=[],
        other_courses=[],
        exempted_courses=[],
        requirement_blocks=requirement_blocks or [],
        exceptions=[],
    )


def _make_block(name: str, status: str = "INCOMPLETE") -> RequirementBlock:
    return RequirementBlock(name=name, status=status, still_needed_text="")


def _make_definition(
    req_id: str = "test_req",
    label: str = "Test Requirement",
    satisfied: bool = False,
    satisfying_courses: list[str] | None = None,
    matching_attributes: frozenset[str] | None = None,
    notes: str = "",
    subject_predicate: str | None = None,
    auto_registered: bool = False,
    block_patterns: tuple[str, ...] = ("Test Block",),
) -> RequirementDefinition:
    return RequirementDefinition(
        item=RequirementItem(
            id=req_id,
            label=label,
            satisfied=satisfied,
            satisfying_courses=satisfying_courses or [],
            matching_attributes=matching_attributes or frozenset(),
            notes=notes,
            subject_predicate=subject_predicate,
            auto_registered=auto_registered,
        ),
        block_patterns=block_patterns,
    )


def _make_section(
    subject: str = "CPSC",
    number: str = "031",
    credits: str = "1",
    distribution: frozenset[str] | None = None,
    course_type: str = "Course",
) -> CourseSection:
    return CourseSection(
        ref_no="99001",
        subject=subject,
        number=number,
        section_id="01",
        title="Test Course",
        credits=Decimal(credits),
        distribution=distribution or frozenset(),
        enr_limit=20,
        instructors=["Staff"],
        course_type=course_type,
        meeting_times=[
            MeetingTime(days=("M", "W"), start=time(10, 0), end=time(11, 0))
        ],
        note="",
        linked_sections=[],
    )


# ---------------------------------------------------------------------------
# Test 1: complete block marks mapped item satisfied
# ---------------------------------------------------------------------------


def test_complete_block_sets_satisfied_true() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Test Block", "COMPLETE")]
    )
    defs = [_make_definition(block_patterns=("Test Block",))]
    status = build_requirement_status(student, defs)
    assert status.items[0].satisfied is True


# ---------------------------------------------------------------------------
# Test 2: incomplete block marks mapped item unsatisfied
# ---------------------------------------------------------------------------


def test_incomplete_block_sets_satisfied_false() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Test Block", "INCOMPLETE")]
    )
    defs = [_make_definition(block_patterns=("Test Block",))]
    status = build_requirement_status(student, defs)
    assert status.items[0].satisfied is False


# ---------------------------------------------------------------------------
# Test 3: CPSC 031 remains unsatisfied despite CR in completed courses
# ---------------------------------------------------------------------------


def test_cpsc031_unsatisfied_despite_cr_in_transcript() -> None:
    """Degree Works (not transcript) is authoritative.

    The "Intro to Computer Systems" block is INCOMPLETE in DW even though the student
    has CPSC 031 with a CR grade. build_requirement_status must honour DW, not the transcript.
    """
    student = _make_student(
        requirement_blocks=[
            _make_block("Intro to Computer Systems", "INCOMPLETE"),
        ]
    )
    defs = [
        _make_definition(
            req_id="cs_cpsc031",
            satisfying_courses=["CPSC 031"],
            block_patterns=("Intro to Computer Systems",),
        )
    ]
    status = build_requirement_status(student, defs)
    assert status.items[0].satisfied is False


# ---------------------------------------------------------------------------
# Test 4: input RequirementDefinition items are not mutated
# ---------------------------------------------------------------------------


def test_input_items_not_mutated() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Test Block", "COMPLETE")]
    )
    defn = _make_definition(block_patterns=("Test Block",))
    original_satisfied = defn.item.satisfied  # False
    original_courses = list(defn.item.satisfying_courses)

    build_requirement_status(student, [defn])

    assert defn.item.satisfied == original_satisfied  # still False
    assert defn.item.satisfying_courses == original_courses


# ---------------------------------------------------------------------------
# Test 5: StudentRecord is not mutated
# ---------------------------------------------------------------------------


def test_student_record_not_mutated() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Test Block", "COMPLETE")]
    )
    original_blocks = deepcopy(student.requirement_blocks)
    defs = [_make_definition(block_patterns=("Test Block",))]
    build_requirement_status(student, defs)
    for orig, current in zip(original_blocks, student.requirement_blocks):
        assert orig.name == current.name
        assert orig.status == current.status


# ---------------------------------------------------------------------------
# Test 6a: credits_remaining = 32 - 31.5 = 0.5
# ---------------------------------------------------------------------------


def test_credits_remaining_is_decimal() -> None:
    student = _make_student(credits_required="32", credits_applied="31.5")
    defs: list[RequirementDefinition] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        status = build_requirement_status(student, defs)
    assert status.credits_remaining == Decimal("0.5")
    assert isinstance(status.credits_remaining, Decimal)


# ---------------------------------------------------------------------------
# Test 6b: credits_remaining clamps to 0 when applied > required
# ---------------------------------------------------------------------------


def test_credits_remaining_clamps_to_zero() -> None:
    student = _make_student(credits_required="32", credits_applied="33")
    defs: list[RequirementDefinition] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        status = build_requirement_status(student, defs)
    assert status.credits_remaining == Decimal("0")


# ---------------------------------------------------------------------------
# Test 7: duplicate requirement IDs raise ValueError
# ---------------------------------------------------------------------------


def test_duplicate_requirement_ids_raise() -> None:
    student = _make_student()
    defs = [
        _make_definition(req_id="dup"),
        _make_definition(req_id="dup"),
    ]
    with pytest.raises(ValueError, match="Duplicate requirement ID"):
        build_requirement_status(student, defs)


# ---------------------------------------------------------------------------
# Test 8: missing matching block leaves item unsatisfied (no error)
# ---------------------------------------------------------------------------


def test_unmatched_block_leaves_item_unsatisfied() -> None:
    student = _make_student(requirement_blocks=[])
    defs = [_make_definition(block_patterns=("Nonexistent Block",))]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        status = build_requirement_status(student, defs)
    assert status.items[0].satisfied is False
    assert any("Nonexistent Block" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# Test 9: multiple blocks matching same definition raises RequirementEvaluationError
# ---------------------------------------------------------------------------


def test_multiple_matching_blocks_raise() -> None:
    student = _make_student(
        requirement_blocks=[
            _make_block("Writing Requirement A", "INCOMPLETE"),
            _make_block("Writing Requirement B", "COMPLETE"),
        ]
    )
    defs = [_make_definition(block_patterns=("Writing Requirement",))]
    with pytest.raises(RequirementEvaluationError, match="multiple blocks"):
        build_requirement_status(student, defs)


# ---------------------------------------------------------------------------
# Test 10: requirement order is preserved
# ---------------------------------------------------------------------------


def test_requirement_order_preserved() -> None:
    student = _make_student(
        requirement_blocks=[
            _make_block("Block A", "COMPLETE"),
            _make_block("Block B", "INCOMPLETE"),
            _make_block("Block C", "COMPLETE"),
        ]
    )
    defs = [
        _make_definition(req_id="first", block_patterns=("Block A",)),
        _make_definition(req_id="second", block_patterns=("Block B",)),
        _make_definition(req_id="third", block_patterns=("Block C",)),
    ]
    status = build_requirement_status(student, defs)
    assert [item.id for item in status.items] == ["first", "second", "third"]
    assert [item.satisfied for item in status.items] == [True, False, True]


# ---------------------------------------------------------------------------
# Test 11: satisfied items excluded from items_satisfied_by
# ---------------------------------------------------------------------------


def test_satisfied_items_excluded_from_items_satisfied_by() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Test Block", "COMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="done_req",
            satisfying_courses=["CPSC 031"],
            block_patterns=("Test Block",),
        )
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(subject="CPSC", number="031", credits="1")
    gains = status.items_satisfied_by(section)
    assert gains == [], "Satisfied items must not appear in items_satisfied_by"


# ---------------------------------------------------------------------------
# Test 12: auto_registered items excluded from items_satisfied_by
# ---------------------------------------------------------------------------


def test_auto_registered_excluded_from_items_satisfied_by() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Test Block", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="auto_req",
            satisfying_courses=["CPSC 099"],
            auto_registered=True,
            block_patterns=("Test Block",),
        )
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(subject="CPSC", number="099", credits="0")
    gains = status.items_satisfied_by(section)
    assert gains == [], "Auto-registered items must not appear in items_satisfied_by"


# ---------------------------------------------------------------------------
# Test 13: section matching multiple predicates appears only once in results
# ---------------------------------------------------------------------------


def test_multi_predicate_match_returns_item_once() -> None:
    """A section satisfying both code and subject predicate returns one item, not two."""
    student = _make_student(
        requirement_blocks=[_make_block("CPSC Block", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="cpsc031",
            satisfying_courses=["CPSC 031"],
            subject_predicate="CPSC",
            block_patterns=("CPSC Block",),
        )
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(subject="CPSC", number="031", credits="1")
    gains = status.items_satisfied_by(section)
    # The item should appear exactly once despite matching both code and subject
    assert len(gains) == 1
    assert gains[0].id == "cpsc031"


# ---------------------------------------------------------------------------
# Test 14: HUW section matches writing item
# ---------------------------------------------------------------------------


def test_huw_section_matches_writing_item() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Writing Requirement", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="writing_hu_or_ns",
            matching_attributes=frozenset({"HUW", "NSW"}),
            block_patterns=("Writing Requirement",),
        )
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(
        subject="ARTH",
        number="001N",
        distribution=frozenset({"HU", "W", "HUW"}),
    )
    gains = status.items_satisfied_by(section)
    assert any(item.id == "writing_hu_or_ns" for item in gains)


# ---------------------------------------------------------------------------
# Test 15: NSW section matches writing item
# ---------------------------------------------------------------------------


def test_nsw_section_matches_writing_item() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Writing Requirement", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="writing_hu_or_ns",
            matching_attributes=frozenset({"HUW", "NSW"}),
            block_patterns=("Writing Requirement",),
        )
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(
        subject="TSTU",
        number="099",
        distribution=frozenset({"NS", "NSEP", "NSW", "W"}),
    )
    gains = status.items_satisfied_by(section)
    assert any(item.id == "writing_hu_or_ns" for item in gains)


# ---------------------------------------------------------------------------
# Test 16: SSW-only section does not match writing item
# ---------------------------------------------------------------------------


def test_ssw_section_does_not_match_writing_item() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Writing Requirement", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="writing_hu_or_ns",
            matching_attributes=frozenset({"HUW", "NSW"}),
            block_patterns=("Writing Requirement",),
        )
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(
        subject="HIST",
        number="010",
        distribution=frozenset({"SS", "W", "SSW"}),
    )
    gains = status.items_satisfied_by(section)
    assert not any(item.id == "writing_hu_or_ns" for item in gains)


# ---------------------------------------------------------------------------
# Test 17: CPSC 031 matches both cs_cpsc031 and cs_cpsc_credits items
# ---------------------------------------------------------------------------


def test_cpsc031_section_matches_two_items() -> None:
    student = _make_student(
        requirement_blocks=[
            _make_block("Intro to Computer Systems", "INCOMPLETE"),
            _make_block("Required Credits in CPSC", "INCOMPLETE"),
        ]
    )
    defs = [
        _make_definition(
            req_id="cs_cpsc031",
            satisfying_courses=["CPSC 031"],
            block_patterns=("Intro to Computer Systems",),
        ),
        _make_definition(
            req_id="cs_cpsc_credits",
            subject_predicate="CPSC",
            block_patterns=("Required Credits in CPSC",),
        ),
    ]
    status = build_requirement_status(student, defs)
    section = _make_section(subject="CPSC", number="031", credits="1")
    gains = status.items_satisfied_by(section)
    gain_ids = {item.id for item in gains}
    assert "cs_cpsc031" in gain_ids
    assert "cs_cpsc_credits" in gain_ids


# ---------------------------------------------------------------------------
# Test 18: zero-credit CPSC section does not match cs_cpsc_credits
# ---------------------------------------------------------------------------


def test_zero_credit_cpsc_does_not_match_cpsc_credits() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Required Credits in CPSC", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="cs_cpsc_credits",
            subject_predicate="CPSC",
            block_patterns=("Required Credits in CPSC",),
        )
    ]
    status = build_requirement_status(student, defs)
    lab_section = _make_section(
        subject="CPSC",
        number="031",
        credits="0",
        course_type="Lab",
    )
    gains = status.items_satisfied_by(lab_section)
    assert not any(item.id == "cs_cpsc_credits" for item in gains)


# ---------------------------------------------------------------------------
# Test 19: non-CPSC one-credit course does not match cs_cpsc_credits
# ---------------------------------------------------------------------------


def test_non_cpsc_section_does_not_match_cpsc_credits() -> None:
    student = _make_student(
        requirement_blocks=[_make_block("Required Credits in CPSC", "INCOMPLETE")]
    )
    defs = [
        _make_definition(
            req_id="cs_cpsc_credits",
            subject_predicate="CPSC",
            block_patterns=("Required Credits in CPSC",),
        )
    ]
    status = build_requirement_status(student, defs)
    engr_section = _make_section(subject="ENGR", number="028", credits="1")
    gains = status.items_satisfied_by(engr_section)
    assert not any(item.id == "cs_cpsc_credits" for item in gains)


# ---------------------------------------------------------------------------
# Test 20: results are deterministic across repeated calls
# ---------------------------------------------------------------------------


def test_results_are_deterministic() -> None:
    student = _make_student(
        requirement_blocks=[
            _make_block("Intro to Computer Systems", "INCOMPLETE"),
            _make_block("Required Credits in CPSC", "COMPLETE"),
        ]
    )
    defs = [
        _make_definition(req_id="a", block_patterns=("Intro to Computer Systems",)),
        _make_definition(req_id="b", block_patterns=("Required Credits in CPSC",)),
    ]
    status_1 = build_requirement_status(student, defs)
    status_2 = build_requirement_status(student, defs)
    for i1, i2 in zip(status_1.items, status_2.items):
        assert i1.id == i2.id
        assert i1.satisfied == i2.satisfied
    assert status_1.credits_remaining == status_2.credits_remaining


# ---------------------------------------------------------------------------
# Test 21: block name normalization — dash variants and extra whitespace
# ---------------------------------------------------------------------------


def test_block_name_normalization() -> None:
    """Degree Works may use em-dash or extra whitespace in block names."""
    student = _make_student(
        # em-dash and extra interior whitespace in block name
        requirement_blocks=[
            _make_block("Distribution  Requirement — Writing", "INCOMPLETE")
        ]
    )
    defs = [
        _make_definition(
            req_id="writing",
            block_patterns=("Distribution Requirement - Writing",),
        )
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no unmatched warning expected
        status = build_requirement_status(student, defs)
    assert status.items[0].satisfied is False  # INCOMPLETE → unsatisfied


# ---------------------------------------------------------------------------
# Integration test: synthetic audit + sample catalog
# ---------------------------------------------------------------------------


def test_integration_synthetic_audit_and_catalog() -> None:
    """End-to-end pipeline: parse fixtures, build status, verify items_satisfied_by."""
    from app.adapters.swarthmore.requirement_defs import get_requirement_definitions
    from app.parsers.audit import parse_audit
    from app.parsers.catalog import parse_catalog

    student = parse_audit(FIXTURES / "audit_synthetic.pdf")
    sections = parse_catalog(FIXTURES / "catalog_sample_10.csv")
    definitions = get_requirement_definitions(student)

    # The synthetic audit uses high-level blocks that don't match CS sub-blocks;
    # items will all remain unsatisfied (unmatched warning is expected).
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        status = build_requirement_status(student, definitions)

    assert len(status.items) == 4
    # Unmatched warnings expected (synthetic blocks don't have CS sub-level names)
    assert len(caught) > 0

    # Locate catalog sections by course code
    section_by_code = {s.course_code: s for s in sections}

    cpsc031 = section_by_code["CPSC 031"]
    cpsc031_gains = status.items_satisfied_by(cpsc031)
    gain_ids = {item.id for item in cpsc031_gains}
    assert "cs_cpsc031" in gain_ids, "CPSC 031 must match cs_cpsc031 item"
    assert "cs_cpsc_credits" in gain_ids, "CPSC 031 must match cs_cpsc_credits item"

    # ARTH 001N has HUW distribution → matches writing
    arth001n = section_by_code["ARTH 001N"]
    writing_gains = status.items_satisfied_by(arth001n)
    assert any(item.id == "writing_hu_or_ns" for item in writing_gains), (
        "HUW section must match writing_hu_or_ns"
    )

    # TSTU 099 has NSW distribution → also matches writing
    tstu099 = section_by_code["TSTU 099"]
    nsw_gains = status.items_satisfied_by(tstu099)
    assert any(item.id == "writing_hu_or_ns" for item in nsw_gains), (
        "NSW section must match writing_hu_or_ns"
    )

    # ANCH 042 has only SS → matches nothing
    anch042 = section_by_code["ANCH 042"]
    anch_gains = status.items_satisfied_by(anch042)
    assert anch_gains == [], "SS-only section must not match any remaining requirement"

    # Senior comp must never appear in items_satisfied_by (auto_registered)
    for section in sections:
        gains = status.items_satisfied_by(section)
        assert not any(item.id == "cs_senior_comp" for item in gains), (
            f"cs_senior_comp must not appear in items_satisfied_by for {section.course_code}"
        )

    # credits_remaining must use Decimal
    assert isinstance(status.credits_remaining, Decimal)

    # Determinism check
    status_2 = build_requirement_status(student, definitions)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        status_2 = build_requirement_status(student, definitions)
    for i1, i2 in zip(status.items, status_2.items):
        assert i1.id == i2.id
        assert i1.satisfied == i2.satisfied
