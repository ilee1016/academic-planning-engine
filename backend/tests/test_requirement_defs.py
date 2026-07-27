"""Tests for adapters/swarthmore/requirement_defs.py.

All tests use synthetic StudentRecord objects; no file I/O.
"""
from __future__ import annotations

import pytest

from app.adapters.swarthmore.requirement_defs import (
    UnsupportedProgramError,
    get_requirement_definitions,
)
from app.models import RequirementDefinition, StudentRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from datetime import date
from decimal import Decimal


def _make_student(
    major: str = "Computer Science",
    catalog_year: str = "202304",
) -> StudentRecord:
    return StudentRecord(
        name="Student, Demo",
        student_id="000000000",
        major=major,
        class_year=2025,
        catalog_year=catalog_year,
        credits_required=Decimal("32"),
        credits_applied=Decimal("29"),
        audit_date=date(2026, 1, 1),
        completed_courses=[],
        preregistered_courses=[],
        other_courses=[],
        exempted_courses=[],
        requirement_blocks=[],
        exceptions=[],
    )


# ---------------------------------------------------------------------------
# Test 1: supported combination returns expected IDs
# ---------------------------------------------------------------------------


def test_cs_202304_returns_expected_ids() -> None:
    defs = get_requirement_definitions(_make_student())
    ids = [d.item.id for d in defs]
    assert ids == [
        "writing_hu_or_ns",
        "cs_cpsc031",
        "cs_cpsc_credits",
        "cs_senior_comp",
    ]


# ---------------------------------------------------------------------------
# Test 2: IDs are unique
# ---------------------------------------------------------------------------


def test_ids_are_unique() -> None:
    defs = get_requirement_definitions(_make_student())
    ids = [d.item.id for d in defs]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Test 3: matching_attributes are frozenset
# ---------------------------------------------------------------------------


def test_matching_attributes_are_frozensets() -> None:
    defs = get_requirement_definitions(_make_student())
    for defn in defs:
        assert isinstance(defn.item.matching_attributes, frozenset), (
            f"{defn.item.id}.matching_attributes is {type(defn.item.matching_attributes)}"
        )


# ---------------------------------------------------------------------------
# Test 4: subject_predicate is "CPSC" only for cs_cpsc_credits
# ---------------------------------------------------------------------------


def test_subject_predicate_only_on_cpsc_credits() -> None:
    defs = get_requirement_definitions(_make_student())
    defn_by_id = {d.item.id: d for d in defs}
    assert defn_by_id["cs_cpsc_credits"].item.subject_predicate == "CPSC"
    for req_id in ("writing_hu_or_ns", "cs_cpsc031", "cs_senior_comp"):
        assert defn_by_id[req_id].item.subject_predicate is None, (
            f"{req_id} should have no subject_predicate"
        )


# ---------------------------------------------------------------------------
# Test 5: auto_registered=True only for cs_senior_comp
# ---------------------------------------------------------------------------


def test_auto_registered_only_for_senior_comp() -> None:
    defs = get_requirement_definitions(_make_student())
    defn_by_id = {d.item.id: d for d in defs}
    assert defn_by_id["cs_senior_comp"].item.auto_registered is True
    for req_id in ("writing_hu_or_ns", "cs_cpsc031", "cs_cpsc_credits"):
        assert defn_by_id[req_id].item.auto_registered is False, (
            f"{req_id} should not be auto_registered"
        )


# ---------------------------------------------------------------------------
# Test 6: repeated calls return independent objects
# ---------------------------------------------------------------------------


def test_repeated_calls_return_independent_objects() -> None:
    student = _make_student()
    defs_a = get_requirement_definitions(student)
    defs_b = get_requirement_definitions(student)
    # Objects must not be the same identity
    for a, b in zip(defs_a, defs_b):
        assert a is not b
        assert a.item is not b.item
        assert a.item.satisfying_courses is not b.item.satisfying_courses


# ---------------------------------------------------------------------------
# Test 7: mutating returned satisfying_courses does not affect next call
# ---------------------------------------------------------------------------


def test_mutating_returned_list_does_not_affect_next_call() -> None:
    student = _make_student()
    defs_a = get_requirement_definitions(student)
    cpsc031_defn = next(d for d in defs_a if d.item.id == "cs_cpsc031")
    cpsc031_defn.item.satisfying_courses.append("FAKE 999")

    defs_b = get_requirement_definitions(student)
    cpsc031_defn_b = next(d for d in defs_b if d.item.id == "cs_cpsc031")
    assert "FAKE 999" not in cpsc031_defn_b.item.satisfying_courses


# ---------------------------------------------------------------------------
# Test 8: unsupported major raises UnsupportedProgramError
# ---------------------------------------------------------------------------


def test_unsupported_major_raises() -> None:
    student = _make_student(major="Mathematics")
    with pytest.raises(UnsupportedProgramError, match="major='Mathematics'"):
        get_requirement_definitions(student)


# ---------------------------------------------------------------------------
# Test 9: unsupported catalog year raises UnsupportedProgramError
# ---------------------------------------------------------------------------


def test_unsupported_catalog_year_raises() -> None:
    student = _make_student(catalog_year="202001")
    with pytest.raises(UnsupportedProgramError, match="catalog_year='202001'"):
        get_requirement_definitions(student)


# ---------------------------------------------------------------------------
# Test 10: major whitespace is handled (stripped before matching)
# ---------------------------------------------------------------------------


def test_major_with_surrounding_whitespace_is_accepted() -> None:
    student = _make_student(major="  Computer Science  ")
    defs = get_requirement_definitions(student)
    assert len(defs) == 4


# ---------------------------------------------------------------------------
# Test 11 (bonus): no adapter import from core
# ---------------------------------------------------------------------------


def test_adapter_does_not_import_core() -> None:
    import importlib
    import sys

    # Force reload to catch imports at module level
    mod_name = "app.adapters.swarthmore.requirement_defs"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])

    mod = sys.modules[mod_name]
    # Walk the module's globals for any core references
    for name, obj in vars(mod).items():
        if hasattr(obj, "__module__") and obj.__module__ is not None:
            assert not obj.__module__.startswith("app.core"), (
                f"Adapter imports core symbol {name!r} from {obj.__module__!r}"
            )


# ---------------------------------------------------------------------------
# Test 12: block_patterns are tuples (immutable)
# ---------------------------------------------------------------------------


def test_block_patterns_are_tuples() -> None:
    defs = get_requirement_definitions(_make_student())
    for defn in defs:
        assert isinstance(defn.block_patterns, tuple), (
            f"{defn.item.id}.block_patterns is {type(defn.block_patterns)}"
        )
        assert len(defn.block_patterns) >= 1


# ---------------------------------------------------------------------------
# Test 13: writing item has both HUW and NSW in matching_attributes
# ---------------------------------------------------------------------------


def test_writing_item_has_huw_and_nsw() -> None:
    defs = get_requirement_definitions(_make_student())
    writing = next(d for d in defs if d.item.id == "writing_hu_or_ns")
    assert "HUW" in writing.item.matching_attributes
    assert "NSW" in writing.item.matching_attributes


# ---------------------------------------------------------------------------
# Test 14: cs_cpsc031 has exactly ["CPSC 031"] in satisfying_courses
# ---------------------------------------------------------------------------


def test_cpsc031_item_satisfying_courses() -> None:
    defs = get_requirement_definitions(_make_student())
    cpsc031 = next(d for d in defs if d.item.id == "cs_cpsc031")
    assert cpsc031.item.satisfying_courses == ["CPSC 031"]


# ---------------------------------------------------------------------------
# Test 15: all items initially unsatisfied
# ---------------------------------------------------------------------------


def test_all_items_initially_unsatisfied() -> None:
    defs = get_requirement_definitions(_make_student())
    for defn in defs:
        assert defn.item.satisfied is False, f"{defn.item.id} should be unsatisfied"


# ---------------------------------------------------------------------------
# Test 16: RequirementDefinition is hashable (frozen dataclass)
# ---------------------------------------------------------------------------


def test_requirement_definition_is_frozen() -> None:
    defs = get_requirement_definitions(_make_student())
    defn = defs[0]
    # frozen dataclass: assignment to its fields should raise
    with pytest.raises((AttributeError, TypeError)):
        defn.block_patterns = ("new pattern",)  # type: ignore[misc]
