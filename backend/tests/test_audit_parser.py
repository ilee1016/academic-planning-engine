"""Tests for backend/app/parsers/audit.py — Stage 2 acceptance criteria.

Unit tests call _parse_audit_text() directly with synthetic text strings.
The PDF golden-file integration test calls parse_audit(Path(...)).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.audit import (
    AuditParseError,
    _attempt_key,
    _parse_credit,
    _parse_exemptions,
    _parse_header,
    _reconcile_course_classification,
    _try_parse_course_line,
    _parse_audit_text,
    parse_audit,
)
from app.models import CompletedCourse, RequirementBlock, StudentRecord

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_PDF = FIXTURE_DIR / "audit_synthetic.pdf"
SYNTHETIC_EXPECTED = FIXTURE_DIR / "audit_synthetic_expected.json"


# ---------------------------------------------------------------------------
# Minimal valid header fragment (reused across many tests)
# ---------------------------------------------------------------------------

_VALID_HEADER = """\
Student name Student, Demo
Student ID 000000000
Degree Bachelor of Arts
Audit date 06/01/2026 8:00 AM
Major Computer Science Advisor Smith, Jane Class Year 2025 Catalog Year 202304 Pronoun they-them
Credits required: 32 Credits applied: 28
"""

_VALID_BLOCK = """\
Degree in Bachelor of Arts
INCOMPLETE
Credits required: 32 Credits applied: 28
Still needed: Complete 4 more credits.
"""

_MINIMAL_AUDIT = _VALID_HEADER + "\n" + _VALID_BLOCK


# ---------------------------------------------------------------------------
# _parse_credit
# ---------------------------------------------------------------------------


class TestParseCredit:
    def test_integer(self) -> None:
        assert _parse_credit("1") == Decimal("1")

    def test_half(self) -> None:
        assert _parse_credit("0.5") == Decimal("0.5")

    def test_parenthesized_one(self) -> None:
        assert _parse_credit("(1)") == Decimal("1")

    def test_parenthesized_zero(self) -> None:
        assert _parse_credit("(0)") == Decimal("0")

    def test_parenthesized_half(self) -> None:
        assert _parse_credit("(0.5)") == Decimal("0.5")

    def test_two(self) -> None:
        assert _parse_credit("2") == Decimal("2")

    def test_result_is_decimal(self) -> None:
        assert isinstance(_parse_credit("1"), Decimal)


# ---------------------------------------------------------------------------
# _parse_header
# ---------------------------------------------------------------------------


class TestParseHeader:
    def test_valid_header(self) -> None:
        h = _parse_header(_VALID_HEADER)
        assert h.name == "Student, Demo"
        assert h.student_id == "000000000"
        assert h.major == "Computer Science"
        assert h.class_year == 2025
        assert h.catalog_year == "202304"
        assert h.audit_date == date(2026, 6, 1)
        assert h.credits_required == Decimal("32")
        assert h.credits_applied == Decimal("28")

    def test_missing_student_name_raises(self) -> None:
        text = _VALID_HEADER.replace("Student name Student, Demo\n", "")
        with pytest.raises(AuditParseError, match="student name"):
            _parse_header(text)

    def test_missing_student_id_raises(self) -> None:
        text = _VALID_HEADER.replace("Student ID 000000000\n", "")
        with pytest.raises(AuditParseError, match="student ID"):
            _parse_header(text)

    def test_missing_class_year_raises(self) -> None:
        text = _VALID_HEADER.replace(
            "Major Computer Science Advisor Smith, Jane Class Year 2025 Catalog Year 202304 Pronoun they-them",
            "Major Computer Science Advisor Smith, Jane Catalog Year 202304 Pronoun they-them",
        )
        with pytest.raises(AuditParseError, match="class year"):
            _parse_header(text)

    def test_missing_audit_date_raises(self) -> None:
        text = _VALID_HEADER.replace("Audit date 06/01/2026 8:00 AM\n", "")
        with pytest.raises(AuditParseError, match="audit date"):
            _parse_header(text)

    def test_missing_credits_raises(self) -> None:
        text = _VALID_HEADER.replace("Credits required: 32 Credits applied: 28\n", "")
        with pytest.raises(AuditParseError, match="credit totals"):
            _parse_header(text)

    def test_invalid_date_format_raises(self) -> None:
        text = _VALID_HEADER.replace("06/01/2026", "2026-06-01")
        with pytest.raises(AuditParseError, match="audit date"):
            _parse_header(text)

    def test_credits_are_decimal(self) -> None:
        h = _parse_header(_VALID_HEADER)
        assert isinstance(h.credits_required, Decimal)
        assert isinstance(h.credits_applied, Decimal)


# ---------------------------------------------------------------------------
# _try_parse_course_line
# ---------------------------------------------------------------------------


class TestTryParseCourseLines:
    def test_letter_grade(self) -> None:
        cc = _try_parse_course_line(
            "CPSC 035 Data Structures and Algorithms C 1 Spring 2024"
        )
        assert cc is not None
        assert cc.code == "CPSC 035"
        assert cc.title == "Data Structures and Algorithms"
        assert cc.grade == "C"
        assert cc.credits == Decimal("1")
        assert cc.term == "Spring 2024"

    def test_cr_grade(self) -> None:
        cc = _try_parse_course_line(
            "CPSC 031 Intro to Computer Systems CR 1 Fall 2023"
        )
        assert cc is not None
        assert cc.grade == "CR"
        assert cc.credits == Decimal("1")

    def test_w_grade(self) -> None:
        cc = _try_parse_course_line("ENGR 073 Physical Electronics W 0 Fall 2025")
        assert cc is not None
        assert cc.grade == "W"
        assert cc.credits == Decimal("0")

    def test_preregistered_grade_dashes(self) -> None:
        cc = _try_parse_course_line(
            "CPSC 063 Artificial Intelligence ---- (1) Fall 2026"
        )
        assert cc is not None
        assert cc.grade == "----"
        assert cc.credits == Decimal("1")

    def test_parenthesized_zero_credit(self) -> None:
        cc = _try_parse_course_line(
            "PHED 002A Fitness Training- Fall I ---- (0) Fall 2026"
        )
        assert cc is not None
        assert cc.code == "PHED 002A"
        assert cc.credits == Decimal("0")

    def test_block_prefix_stripped(self) -> None:
        cc = _try_parse_course_line(
            "Group 1 CPSC 041 Algorithms B+ 1 Spring 2025"
        )
        assert cc is not None
        assert cc.code == "CPSC 041"
        assert cc.title == "Algorithms"
        assert cc.grade == "B+"

    def test_half_credit(self) -> None:
        cc = _try_parse_course_line("MUSI 048 Perfor-Indiv Instruction CR 0.5 Spring 2026")
        assert cc is not None
        assert cc.credits == Decimal("0.5")

    def test_a_minus_grade(self) -> None:
        cc = _try_parse_course_line("MUSI 002B Music Basics A- 1 Fall 2025")
        assert cc is not None
        assert cc.grade == "A-"

    def test_no_term_returns_none(self) -> None:
        assert _try_parse_course_line("CPSC 035 Data Structures C 1") is None

    def test_no_course_code_returns_none(self) -> None:
        assert _try_parse_course_line("Still needed: 1 Course in CPSC 031") is None

    def test_ap_subrow_returns_none(self) -> None:
        assert (
            _try_parse_course_line(
                "Satisfied by: NO TRANSCRIPT DETAI - Advanced Placement"
            )
            is None
        )

    def test_exemption_line_returns_none(self) -> None:
        assert (
            _try_parse_course_line("Exempted from CPSC 021 via placement.")
            is None
        )

    def test_course_table_header_returns_none(self) -> None:
        assert (
            _try_parse_course_line(
                "Course Title Grade Credits Term Repeated"
            )
            is None
        )

    def test_credits_are_decimal(self) -> None:
        cc = _try_parse_course_line("MATH 027 Linear Algebra CR 1 Fall 2023")
        assert cc is not None
        assert isinstance(cc.credits, Decimal)

    def test_title_with_parentheses_and_w(self) -> None:
        cc = _try_parse_course_line("LING 040 Semantics (W) A 1 Spring 2026")
        assert cc is not None
        assert cc.code == "LING 040"
        assert cc.grade == "A"
        assert cc.term == "Spring 2026"

    def test_summer_term(self) -> None:
        cc = _try_parse_course_line("SPAN 001 Intro Spanish B 1 Summer 2023")
        assert cc is not None
        assert cc.term == "Summer 2023"


# ---------------------------------------------------------------------------
# _parse_exemptions
# ---------------------------------------------------------------------------


class TestParseExemptions:
    def test_single_exemption(self) -> None:
        text = "Exempted from CPSC 021 via placement."
        assert _parse_exemptions(text) == ["CPSC 021"]

    def test_multiple_exemptions(self) -> None:
        text = (
            "Exempted from CPSC 021 via placement.\n"
            "Exempted from MATH 015 via placement."
        )
        assert _parse_exemptions(text) == ["CPSC 021", "MATH 015"]

    def test_duplicate_deduplicated(self) -> None:
        text = (
            "Exempted from CPSC 021 via placement.\n"
            "Exempted from CPSC 021 via placement."
        )
        assert _parse_exemptions(text) == ["CPSC 021"]

    def test_no_exemptions(self) -> None:
        assert _parse_exemptions("No exemptions here.") == []

    def test_extra_whitespace_tolerated(self) -> None:
        text = "Exempted from CPSC  021 via placement."
        # The regex allows flexible spacing between subject and number
        result = _parse_exemptions(text)
        assert "CPSC 021" in result


# ---------------------------------------------------------------------------
# Requirement block extraction (via _parse_audit_text)
# ---------------------------------------------------------------------------


_SINGLE_BLOCK = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Credits required: 32 Credits applied: 28
Still needed: Complete 4 more credits and all requirements.
"""

_COMPLETE_BLOCK = _VALID_HEADER + """\

20 Credits Outside Major
COMPLETE
Credits required: 20 Credits applied: 21
"""

_MULTI_BLOCK = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Credits required: 32 Credits applied: 28
Still needed: Complete 4 more credits.

20 Credits Outside Major
COMPLETE
Credits required: 20 Credits applied: 21

Major in Computer Science
INCOMPLETE
Still needed: Complete 2 more CPSC credits.
"""


class TestRequirementBlocks:
    def test_single_incomplete_block(self) -> None:
        record = _parse_audit_text(_SINGLE_BLOCK)
        blocks = record.requirement_blocks
        assert len(blocks) == 1
        assert blocks[0].name == "Degree in Bachelor of Arts"
        assert blocks[0].status == "INCOMPLETE"
        assert "Complete 4 more credits" in blocks[0].still_needed_text

    def test_complete_block_has_empty_still_needed(self) -> None:
        record = _parse_audit_text(_COMPLETE_BLOCK)
        block = next(b for b in record.requirement_blocks if b.status == "COMPLETE")
        assert block.still_needed_text == ""

    def test_multiple_consecutive_blocks(self) -> None:
        record = _parse_audit_text(_MULTI_BLOCK)
        names = [b.name for b in record.requirement_blocks]
        assert "Degree in Bachelor of Arts" in names
        assert "20 Credits Outside Major" in names
        assert "Major in Computer Science" in names

    def test_major_in_block_detected(self) -> None:
        record = _parse_audit_text(_MULTI_BLOCK)
        major_block = next(b for b in record.requirement_blocks if b.name.startswith("Major in"))
        assert major_block.status == "INCOMPLETE"

    def test_block_labels_preserved(self) -> None:
        record = _parse_audit_text(_MULTI_BLOCK)
        names = {b.name for b in record.requirement_blocks}
        assert "20 Credits Outside Major" in names

    def test_still_needed_is_not_parsed_as_structured_data(self) -> None:
        record = _parse_audit_text(_SINGLE_BLOCK)
        b = record.requirement_blocks[0]
        # still_needed_text is a plain string; it has no sub-structure
        assert isinstance(b.still_needed_text, str)

    def test_multiline_still_needed_captured(self) -> None:
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Credits required: 32 Credits applied: 28
Still needed: You have 28 credits.
You need 4 more credits and all requirements.
"""
        record = _parse_audit_text(text)
        b = record.requirement_blocks[0]
        assert "28 credits" in b.still_needed_text

    def test_no_blocks_raises(self) -> None:
        # Text with valid header but no COMPLETE/INCOMPLETE markers
        with pytest.raises(AuditParseError, match="No recognizable requirement blocks"):
            _parse_audit_text(_VALID_HEADER)


# ---------------------------------------------------------------------------
# Course classification
# ---------------------------------------------------------------------------


_AUDIT_WITH_COURSES = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Credits required: 32 Credits applied: 28

Major in Computer Science
INCOMPLETE
Still needed: 2 more CPSC credits.

Course Title Grade Credits Term Repeated
CPSC 031 Intro to Computer Systems CR 1 Fall 2023
CPSC 035 Data Structures and Algorithms C 1 Spring 2024
MATH 027 Linear Algebra CR 1 Fall 2023
MATH 035 Several Variable Calc w/Theory B 1 Spring 2024

Additional Courses
Credits: 2 Courses: 2
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026
MUSI 041 Performance- Jazz S 0 Spring 2026

Courses Not Applied
Credits: 0 Courses: 1
Course Title Grade Credits Term Repeated
ENGR 073 Physical Electronics W 0 Fall 2025

Preregistered
Credits: 1 Courses: 1
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026
"""


class TestCourseClassification:
    def _record(self) -> StudentRecord:
        return _parse_audit_text(_AUDIT_WITH_COURSES)

    def test_completed_courses_present(self) -> None:
        rec = self._record()
        codes = {c.code for c in rec.completed_courses}
        assert "CPSC 031" in codes
        assert "CPSC 035" in codes
        assert "MATH 027" in codes

    def test_w_grade_in_other_courses(self) -> None:
        rec = self._record()
        codes = {c.code for c in rec.other_courses}
        assert "ENGR 073" in codes

    def test_additional_course_in_other_courses(self) -> None:
        rec = self._record()
        codes = {c.code for c in rec.other_courses}
        assert "MUSI 041" in codes

    def test_preregistered_course_in_preregistered(self) -> None:
        rec = self._record()
        codes = {c.code for c in rec.preregistered_courses}
        assert "CPSC 063" in codes

    def test_completed_not_duplicated_in_other(self) -> None:
        rec = self._record()
        completed_codes = {c.code for c in rec.completed_courses}
        other_codes = {c.code for c in rec.other_courses}
        # CPSC 063 only appears in other/preregistered not completed
        assert "CPSC 063" not in completed_codes

    def test_completed_courses_deduped(self) -> None:
        # MATH 027 appears once in the text but won't appear twice
        rec = self._record()
        math027_count = sum(1 for c in rec.completed_courses if c.code == "MATH 027")
        assert math027_count == 1

    def test_preregistered_grade_is_dashes(self) -> None:
        rec = self._record()
        reg = next(c for c in rec.preregistered_courses if c.code == "CPSC 063")
        assert reg.grade == "----"

    def test_preregistered_credit_is_decimal(self) -> None:
        rec = self._record()
        reg = next(c for c in rec.preregistered_courses if c.code == "CPSC 063")
        assert isinstance(reg.credits, Decimal)
        assert reg.credits == Decimal("1")

    def test_ap_subrow_skipped(self) -> None:
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

Major in Computer Science
INCOMPLETE
Still needed: 2 more credits.
MATH 015 Single-Variable Calculus 1 5 1 Spring 2023
Satisfied by: NO TRANSCRIPT DETAI - Advanced Placement
"""
        rec = _parse_audit_text(text)
        # MATH 015 should be parsed as a course, AP sub-row skipped
        codes = {c.code for c in rec.completed_courses}
        assert "MATH 015" in codes
        # No course with "NO TRANSCRIPT" in title
        for c in rec.completed_courses:
            assert "NO TRANSCRIPT" not in c.title

    def test_duplicate_course_in_multiple_sections_deduped(self) -> None:
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

Major in Computer Science
INCOMPLETE
Still needed: 2 more credits.
CPSC 031 Intro to Computer Systems CR 1 Fall 2023
NSEP Science Lab CPSC 031 Intro to Computer Systems CR 1 Fall 2023
"""
        rec = _parse_audit_text(text)
        cpsc031_count = sum(1 for c in rec.completed_courses if c.code == "CPSC 031")
        assert cpsc031_count == 1


# ---------------------------------------------------------------------------
# Placement exemptions (via _parse_audit_text)
# ---------------------------------------------------------------------------


class TestExemptionsInAudit:
    def test_exemption_extracted(self) -> None:
        text = _VALID_HEADER + """\

Major in Computer Science
INCOMPLETE
Still needed: 2 more CPSC credits.
Exempted from CPSC 021 via placement.
Substitute with another course.
"""
        rec = _parse_audit_text(text)
        assert "CPSC 021" in rec.exempted_courses

    def test_exemption_not_in_completed(self) -> None:
        text = _VALID_HEADER + """\

Major in Computer Science
INCOMPLETE
Still needed: 2 more CPSC credits.
Exempted from CPSC 021 via placement.
"""
        rec = _parse_audit_text(text)
        codes = {c.code for c in rec.completed_courses}
        assert "CPSC 021" not in codes

    def test_no_exemptions(self) -> None:
        rec = _parse_audit_text(_AUDIT_WITH_COURSES)
        # _AUDIT_WITH_COURSES has no exemption line
        assert rec.exempted_courses == []

    def test_multiple_exemptions(self) -> None:
        text = _VALID_HEADER + """\

Major in Computer Science
INCOMPLETE
Still needed: 2 more CPSC credits.
Exempted from CPSC 021 via placement.
Exempted from CPSC 010 via placement.
"""
        rec = _parse_audit_text(text)
        assert "CPSC 021" in rec.exempted_courses
        assert "CPSC 010" in rec.exempted_courses

    def test_duplicate_exemptions_deduped(self) -> None:
        text = _VALID_HEADER + """\

Major in Computer Science
INCOMPLETE
Still needed: 2 more CPSC credits.
Exempted from CPSC 021 via placement.
Exempted from CPSC 021 via placement.
"""
        rec = _parse_audit_text(text)
        assert rec.exempted_courses.count("CPSC 021") == 1


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


_AUDIT_WITH_EXCEPTIONS = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

Exceptions
Type Description Created on Created by Block Enforced
Apply Here Apply CPSC 086 here. 03/26/2026 Jenemann, Usha N Major in Computer Science Yes
Legend
"""


class TestExceptions:
    def test_exception_extracted(self) -> None:
        rec = _parse_audit_text(_AUDIT_WITH_EXCEPTIONS)
        assert len(rec.exceptions) >= 1
        assert any("CPSC 086" in ex for ex in rec.exceptions)

    def test_no_exceptions_section(self) -> None:
        rec = _parse_audit_text(_SINGLE_BLOCK)
        assert rec.exceptions == []

    def test_exception_header_row_excluded(self) -> None:
        rec = _parse_audit_text(_AUDIT_WITH_EXCEPTIONS)
        for ex in rec.exceptions:
            assert "Type Description" not in ex


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_empty_text_raises(self) -> None:
        with pytest.raises(AuditParseError, match="extract text"):
            _parse_audit_text("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(AuditParseError, match="extract text"):
            _parse_audit_text("   \n   \n   ")

    def test_unrecognized_file_raises(self) -> None:
        with pytest.raises(AuditParseError, match="does not appear to be"):
            _parse_audit_text("This is a completely different document.")

    def test_no_requirement_blocks_raises(self) -> None:
        with pytest.raises(AuditParseError, match="No recognizable requirement blocks"):
            _parse_audit_text(_VALID_HEADER)


# ---------------------------------------------------------------------------
# Integrated synthetic audit — full StudentRecord assertion
# ---------------------------------------------------------------------------

_FULL_SYNTHETIC = """\
Student name Student, Demo
Student ID 000000000
Degree Bachelor of Arts
Audit date 06/01/2026 8:00 AM
Major Computer Science Advisor Smith, Jane Class Year 2025 Catalog Year 202304 Pronoun they-them
Credits required: 32 Credits applied: 29.5

Degree in Bachelor of Arts
INCOMPLETE
Credits required: 32 Credits applied: 29.5
Still needed: Complete 2.5 more credits.

20 Credits Outside Major
COMPLETE
Credits required: 20 Credits applied: 21

Major in Computer Science
INCOMPLETE
Still needed: 2 more CPSC credits.
Exempted from CPSC 021 via placement.
Substitute with another course from Groups 1-3.

Course Title Grade Credits Term Repeated
CPSC 035 Data Structures and Algorithms C 1 Spring 2024
CPSC 041 Algorithms B+ 1 Spring 2025
MATH 027 Linear Algebra CR 1 Fall 2023
ECON 001 Introduction to Economics B+ 1 Fall 2024

Additional Courses
Credits: 2 Courses: 2
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026
MUSI 041 Performance- Jazz S 0 Spring 2026

Courses Not Applied
Credits: 0 Courses: 1
Course Title Grade Credits Term Repeated
ENGR 073 Physical Electronics W 0 Fall 2025

Preregistered
Credits: 1 Courses: 1
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026

Exceptions
Type Description Created on Created by Block Enforced
Apply Here Apply CPSC 086 here. 03/26/2026 Smith, Jane Computer Science Yes
Legend
"""


class TestIntegratedSyntheticAudit:
    def _record(self) -> StudentRecord:
        return _parse_audit_text(_FULL_SYNTHETIC)

    def test_name(self) -> None:
        assert self._record().name == "Student, Demo"

    def test_student_id(self) -> None:
        assert self._record().student_id == "000000000"

    def test_major(self) -> None:
        assert self._record().major == "Computer Science"

    def test_class_year(self) -> None:
        assert self._record().class_year == 2025

    def test_catalog_year(self) -> None:
        assert self._record().catalog_year == "202304"

    def test_audit_date(self) -> None:
        assert self._record().audit_date == date(2026, 6, 1)

    def test_credits_required(self) -> None:
        assert self._record().credits_required == Decimal("32")

    def test_credits_applied(self) -> None:
        assert self._record().credits_applied == Decimal("29.5")

    def test_completed_courses_present(self) -> None:
        codes = {c.code for c in self._record().completed_courses}
        assert "CPSC 035" in codes
        assert "MATH 027" in codes
        assert "ECON 001" in codes

    def test_preregistered_in_correct_list(self) -> None:
        rec = self._record()
        preregistered = {c.code for c in rec.preregistered_courses}
        assert "CPSC 063" in preregistered

    def test_other_courses_present(self) -> None:
        rec = self._record()
        other = {c.code for c in rec.other_courses}
        assert "ENGR 073" in other or "MUSI 041" in other

    def test_preregistered_not_duplicated_in_other(self) -> None:
        rec = self._record()
        other = {c.code for c in rec.other_courses}
        prereg = {c.code for c in rec.preregistered_courses}
        # CPSC 063 appears in Additional Courses AND Preregistered in _FULL_SYNTHETIC;
        # after reconciliation it must only be in preregistered_courses
        assert "CPSC 063" in prereg
        assert "CPSC 063" not in other

    def test_no_cross_list_duplicate_attempts(self) -> None:
        assert_no_cross_list_duplicate_attempts(self._record())

    def test_exempted_courses(self) -> None:
        assert "CPSC 021" in self._record().exempted_courses

    def test_requirement_blocks_count(self) -> None:
        blocks = self._record().requirement_blocks
        assert len(blocks) >= 3

    def test_incomplete_block_has_still_needed(self) -> None:
        rec = self._record()
        deg = next(
            b for b in rec.requirement_blocks if b.name == "Degree in Bachelor of Arts"
        )
        assert "2.5 more credits" in deg.still_needed_text

    def test_complete_block_still_needed_empty(self) -> None:
        rec = self._record()
        outside = next(
            b for b in rec.requirement_blocks if b.name == "20 Credits Outside Major"
        )
        assert outside.status == "COMPLETE"
        assert outside.still_needed_text == ""

    def test_exceptions_extracted(self) -> None:
        rec = self._record()
        assert any("CPSC 086" in ex for ex in rec.exceptions)

    def test_credits_are_decimal(self) -> None:
        rec = self._record()
        assert isinstance(rec.credits_required, Decimal)
        assert isinstance(rec.credits_applied, Decimal)
        for c in rec.completed_courses + rec.preregistered_courses + rec.other_courses:
            assert isinstance(c.credits, Decimal)


# ---------------------------------------------------------------------------
# Assertion helper
# ---------------------------------------------------------------------------


def assert_no_cross_list_duplicate_attempts(student: StudentRecord) -> None:
    """Assert that no course attempt (code, term, grade, credits) appears in
    more than one of completed_courses, preregistered_courses, other_courses.
    """
    seen: dict[tuple[str, str, str, str], str] = {}
    for label, lst in [
        ("completed", student.completed_courses),
        ("preregistered", student.preregistered_courses),
        ("other", student.other_courses),
    ]:
        for c in lst:
            key = _attempt_key(c)
            if key in seen:
                raise AssertionError(
                    f"Attempt {key} appears in both {seen[key]!r} and {label!r}"
                )
            seen[key] = label


# ---------------------------------------------------------------------------
# Reconciliation tests
# ---------------------------------------------------------------------------


def _make_course(
    code: str,
    term: str,
    grade: str = "B",
    credits: str = "1",
    title: str = "",
) -> CompletedCourse:
    return CompletedCourse(
        code=code, term=term, grade=grade, credits=Decimal(credits), title=title
    )


class TestReconciliation:
    """Tests for _reconcile_course_classification() and its integration
    inside _parse_audit_text()."""

    # --- Direct unit tests of the reconciliation function ---

    def test_preregistered_attempt_removed_from_completed(self) -> None:
        c = _make_course("PHED 002B", "Fall 2026", "----", "0")
        completed, preregistered, other = _reconcile_course_classification(
            [c], [c], []
        )
        assert c not in completed
        assert c in preregistered

    def test_other_attempt_removed_from_completed(self) -> None:
        c = _make_course("CPSC 063", "Fall 2026", "----", "1")
        completed, preregistered, other = _reconcile_course_classification(
            [c], [], [c]
        )
        assert c not in completed
        assert c in other

    def test_preregistered_attempt_removed_from_other(self) -> None:
        c = _make_course("CPSC 063", "Fall 2026", "----", "1")
        completed, preregistered, other = _reconcile_course_classification(
            [], [c], [c]
        )
        assert c in preregistered
        assert c not in other

    def test_distinct_retake_preserved_in_both_lists(self) -> None:
        """Historical (completed) and new attempt (preregistered) coexist."""
        historical = _make_course("CPSC 031", "Fall 2023", "CR", "1")
        retake = _make_course("CPSC 031", "Fall 2026", "----", "1")
        completed, preregistered, other = _reconcile_course_classification(
            [historical], [retake], []
        )
        assert historical in completed
        assert retake in preregistered

    def test_same_code_two_completed_terms_both_remain(self) -> None:
        fall = _make_course("MATH 027", "Fall 2023", "CR", "1")
        spring = _make_course("MATH 027", "Spring 2023", "B", "1")
        completed, preregistered, other = _reconcile_course_classification(
            [fall, spring], [], []
        )
        assert fall in completed
        assert spring in completed

    def test_source_order_preserved(self) -> None:
        a = _make_course("CPSC 035", "Spring 2024", "C", "1", "Data Structures")
        b = _make_course("MATH 027", "Fall 2023", "CR", "1", "Linear Algebra")
        c = _make_course("ECON 001", "Fall 2024", "B+", "1", "Intro Econ")
        completed, _, _ = _reconcile_course_classification([a, b, c], [], [])
        assert completed == [a, b, c]

    # --- Integration tests via _parse_audit_text() ---

    def test_preregistered_in_block_not_in_completed(self) -> None:
        """Course rendered inside a requirement block AND in Preregistered
        must appear only in preregistered_courses after reconciliation."""
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

Distribution Requirements
INCOMPLETE

PHED 002B Fitness Training ---- (0) Fall 2026

Preregistered
Credits: 0 Courses: 1
Course Title Grade Credits Term Repeated
PHED 002B Fitness Training ---- (0) Fall 2026
"""
        rec = _parse_audit_text(text)
        assert_no_cross_list_duplicate_attempts(rec)
        phed_completed = [c for c in rec.completed_courses if c.code == "PHED 002B"]
        phed_prereg = [c for c in rec.preregistered_courses if c.code == "PHED 002B"]
        assert phed_completed == [], "PHED 002B should not be in completed_courses"
        assert len(phed_prereg) == 1

    def test_other_course_not_in_completed(self) -> None:
        """Course in Additional Courses must not appear in completed_courses
        even if the same row appears inside a requirement block."""
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

CPSC 063 Artificial Intelligence ---- (1) Fall 2026

Additional Courses
Credits: 1 Courses: 1
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026
"""
        rec = _parse_audit_text(text)
        assert_no_cross_list_duplicate_attempts(rec)
        in_completed = any(c.code == "CPSC 063" for c in rec.completed_courses)
        in_other = any(c.code == "CPSC 063" for c in rec.other_courses)
        assert not in_completed, "CPSC 063 should not be in completed_courses"
        assert in_other

    def test_historical_completed_and_preregistered_retake_both_present(self) -> None:
        """Different term = distinct attempt; both should survive reconciliation."""
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

CPSC 031 Intro to Computer Systems CR 1 Fall 2023

Preregistered
Credits: 1 Courses: 1
Course Title Grade Credits Term Repeated
CPSC 031 Intro to Computer Systems ---- (1) Fall 2026
"""
        rec = _parse_audit_text(text)
        assert_no_cross_list_duplicate_attempts(rec)
        completed_031 = [c for c in rec.completed_courses if c.code == "CPSC 031"]
        prereg_031 = [c for c in rec.preregistered_courses if c.code == "CPSC 031"]
        assert len(completed_031) == 1, "historical CR attempt should remain completed"
        assert completed_031[0].grade == "CR"
        assert completed_031[0].term == "Fall 2023"
        assert len(prereg_031) == 1, "retake should be in preregistered"
        assert prereg_031[0].grade == "----"

    def test_integrated_no_cross_list_duplicates(self) -> None:
        """The full synthetic audit fixture must have no duplicate attempts."""
        rec = _parse_audit_text(_FULL_SYNTHETIC)
        assert_no_cross_list_duplicate_attempts(rec)

    def test_preregistered_in_additional_not_in_other(self) -> None:
        """Course in both Additional Courses and Preregistered appears only in
        preregistered_courses after reconciliation (preregistered > other)."""
        text = _VALID_HEADER + """\

Degree in Bachelor of Arts
INCOMPLETE
Still needed: Done.

Additional Courses
Credits: 1 Courses: 1
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026

Preregistered
Credits: 1 Courses: 1
Course Title Grade Credits Term Repeated
CPSC 063 Artificial Intelligence ---- (1) Fall 2026
"""
        rec = _parse_audit_text(text)
        assert_no_cross_list_duplicate_attempts(rec)
        in_other = any(c.code == "CPSC 063" for c in rec.other_courses)
        in_prereg = any(c.code == "CPSC 063" for c in rec.preregistered_courses)
        assert not in_other, "preregistered takes precedence over other"
        assert in_prereg


# ---------------------------------------------------------------------------
# Golden-file integration test (synthetic PDF)
# ---------------------------------------------------------------------------


def _record_to_dict(rec: StudentRecord) -> dict:  # type: ignore[type-arg]
    return {
        "name": rec.name,
        "student_id": rec.student_id,
        "major": rec.major,
        "class_year": rec.class_year,
        "catalog_year": rec.catalog_year,
        "credits_required": str(rec.credits_required),
        "credits_applied": str(rec.credits_applied),
        "audit_date": rec.audit_date.isoformat(),
        "completed_courses": [
            {
                "code": c.code,
                "title": c.title,
                "grade": c.grade,
                "credits": str(c.credits),
                "term": c.term,
            }
            for c in rec.completed_courses
        ],
        "preregistered_courses": [
            {
                "code": c.code,
                "title": c.title,
                "grade": c.grade,
                "credits": str(c.credits),
                "term": c.term,
            }
            for c in rec.preregistered_courses
        ],
        "other_courses": [
            {
                "code": c.code,
                "title": c.title,
                "grade": c.grade,
                "credits": str(c.credits),
                "term": c.term,
            }
            for c in rec.other_courses
        ],
        "exempted_courses": rec.exempted_courses,
        "requirement_blocks": [
            {
                "name": b.name,
                "status": b.status,
                "still_needed_text": b.still_needed_text,
            }
            for b in rec.requirement_blocks
        ],
        "exceptions": rec.exceptions,
    }


@pytest.mark.skipif(
    not SYNTHETIC_PDF.exists(),
    reason="Synthetic PDF fixture not yet generated",
)
class TestGoldenFileSyntheticPDF:
    def test_parse_audit_matches_expected_json(self) -> None:
        record = parse_audit(SYNTHETIC_PDF)
        actual = _record_to_dict(record)

        with SYNTHETIC_EXPECTED.open() as f:
            expected = json.load(f)

        assert actual == expected
