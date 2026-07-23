"""Tests for backend/app/parsers/catalog.py — Stage 1 acceptance criteria."""

from __future__ import annotations

import json
import logging
from datetime import time
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from app.parsers.catalog import (
    CatalogParseError,
    _parse_decimal,
    _parse_distribution,
    _parse_instructors,
    _parse_meeting_times,
    _parse_time,
    _strip_excel_formula,
    parse_catalog,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CSV = FIXTURE_DIR / "catalog_sample_10.csv"
SAMPLE_EXPECTED = FIXTURE_DIR / "catalog_sample_10_expected.json"


# ---------------------------------------------------------------------------
# _strip_excel_formula
# ---------------------------------------------------------------------------


class TestStripExcelFormula:
    def test_strips_number(self) -> None:
        assert _strip_excel_formula('="035"') == "035"

    def test_strips_section(self) -> None:
        assert _strip_excel_formula('="01"') == "01"

    def test_alpha_section_unchanged(self) -> None:
        assert _strip_excel_formula("A") == "A"

    def test_plain_string_unchanged(self) -> None:
        assert _strip_excel_formula("MA") == "MA"

    def test_plain_number_unchanged(self) -> None:
        # A plain numeric string (no wrapper) is left alone
        assert _strip_excel_formula("042") == "042"

    def test_partial_wrapper_unchanged(self) -> None:
        # Incomplete wrappers do not match
        assert _strip_excel_formula('="035') == '="035'
        assert _strip_excel_formula('=035"') == '=035"'

    def test_empty_wrapper(self) -> None:
        # ="": strips to empty string
        assert _strip_excel_formula('=""') == ""

    def test_alpha_numeric_value(self) -> None:
        assert _strip_excel_formula('="001N"') == "001N"


# ---------------------------------------------------------------------------
# _parse_decimal
# ---------------------------------------------------------------------------


class TestParseDecimal:
    def test_blank_is_zero(self) -> None:
        assert _parse_decimal("", "REF") == Decimal("0")

    def test_whitespace_is_zero(self) -> None:
        assert _parse_decimal("  ", "REF") == Decimal("0")

    def test_one_credit(self) -> None:
        assert _parse_decimal("1", "REF") == Decimal("1")

    def test_half_credit(self) -> None:
        assert _parse_decimal("0.5", "REF") == Decimal("0.5")

    def test_one_and_half_credits(self) -> None:
        assert _parse_decimal("1.5", "REF") == Decimal("1.5")

    def test_two_credits(self) -> None:
        assert _parse_decimal("2", "REF") == Decimal("2")

    def test_invalid_raises(self) -> None:
        with pytest.raises(CatalogParseError, match="ref_no="):
            _parse_decimal("abc", "30395")

    def test_result_is_decimal_not_float(self) -> None:
        result = _parse_decimal("1", "REF")
        assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# _parse_distribution
# ---------------------------------------------------------------------------


class TestParseDistribution:
    def test_blank_is_empty(self) -> None:
        assert _parse_distribution("") == frozenset()

    def test_single_code(self) -> None:
        assert _parse_distribution("SS") == frozenset({"SS"})

    def test_hu_w_adds_huw(self) -> None:
        result = _parse_distribution("HU, W")
        assert result == frozenset({"HU", "W", "HUW"})

    def test_ns_w_adds_nsw(self) -> None:
        result = _parse_distribution("NS, W")
        assert result == frozenset({"NS", "W", "NSW"})

    def test_ns_nsep_w_adds_nsw_preserves_nsep(self) -> None:
        result = _parse_distribution("NS, NSEP, W")
        assert result == frozenset({"NS", "NSEP", "W", "NSW"})

    def test_ss_w_adds_ssw(self) -> None:
        result = _parse_distribution("SS, W")
        assert result == frozenset({"SS", "W", "SSW"})

    def test_w_alone_adds_ndw(self) -> None:
        result = _parse_distribution("W")
        assert result == frozenset({"W", "NDW"})

    def test_result_is_frozenset(self) -> None:
        result = _parse_distribution("HU")
        assert isinstance(result, frozenset)

    def test_fys_hu_w_adds_huw(self) -> None:
        result = _parse_distribution("FYS, HU, W")
        assert "HUW" in result
        assert "FYS" in result
        assert "NDW" not in result

    def test_ns_nsep_no_w(self) -> None:
        # NS+NSEP without W: no NSW derived
        result = _parse_distribution("NS, NSEP")
        assert result == frozenset({"NS", "NSEP"})
        assert "NSW" not in result


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------


class TestParseTime:
    def test_morning_am(self) -> None:
        assert _parse_time("10:30am") == time(10, 30)

    def test_afternoon_pm(self) -> None:
        assert _parse_time("01:15pm") == time(13, 15)

    def test_noon(self) -> None:
        assert _parse_time("12:00pm") == time(12, 0)

    def test_midnight(self) -> None:
        assert _parse_time("12:00am") == time(0, 0)

    def test_single_digit_hour(self) -> None:
        assert _parse_time("9:55am") == time(9, 55)

    def test_invalid_raises(self) -> None:
        with pytest.raises(CatalogParseError):
            _parse_time("10:30")

    def test_case_insensitive(self) -> None:
        assert _parse_time("10:30AM") == time(10, 30)


# ---------------------------------------------------------------------------
# _parse_meeting_times
# ---------------------------------------------------------------------------


class TestParseMeetingTimes:
    def test_single_meeting(self) -> None:
        result = _parse_meeting_times("MWF", "10:30am-11:20am", "REF")
        assert len(result) == 1
        mt = result[0]
        assert mt.days == ("M", "W", "F")
        assert mt.start == time(10, 30)
        assert mt.end == time(11, 20)

    def test_afternoon_meeting(self) -> None:
        result = _parse_meeting_times("TR", "01:15pm-02:30pm", "REF")
        assert result[0].start == time(13, 15)
        assert result[0].end == time(14, 30)

    def test_single_day(self) -> None:
        result = _parse_meeting_times("W", "01:15pm-04:00pm", "REF")
        assert result[0].days == ("W",)

    def test_multi_meeting_different_times(self) -> None:
        result = _parse_meeting_times("M,M", "02:31pm-04:30pm,01:15pm-02:30pm", "REF")
        assert len(result) == 2
        assert result[0].start == time(14, 31)
        assert result[1].start == time(13, 15)

    def test_multi_meeting_duplicate_deduplicated(self) -> None:
        result = _parse_meeting_times("M,M", "01:15pm-04:15pm,01:15pm-04:15pm", "REF")
        assert len(result) == 1

    def test_empty_days_and_times(self) -> None:
        assert _parse_meeting_times("", "", "REF") == []

    def test_only_days_empty_raises(self) -> None:
        with pytest.raises(CatalogParseError, match="one of Days/Times is empty"):
            _parse_meeting_times("", "10:30am-11:20am", "REF")

    def test_only_times_empty_raises(self) -> None:
        with pytest.raises(CatalogParseError, match="one of Days/Times is empty"):
            _parse_meeting_times("MWF", "", "REF")

    def test_mismatched_count_raises(self) -> None:
        with pytest.raises(CatalogParseError, match="day group"):
            _parse_meeting_times("M,W", "10:30am-11:20am", "REF")

    def test_start_equals_end_raises(self) -> None:
        with pytest.raises(CatalogParseError, match="start .* >= end"):
            _parse_meeting_times("M", "10:30am-10:30am", "REF")

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(CatalogParseError, match="start .* >= end"):
            _parse_meeting_times("M", "11:00am-10:00am", "REF")

    def test_back_to_back_is_valid(self) -> None:
        # Two different meetings that are back-to-back should not be deduplicated
        result = _parse_meeting_times("M,M", "09:00am-10:00am,10:00am-11:00am", "REF")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _parse_instructors
# ---------------------------------------------------------------------------


class TestParseInstructors:
    def test_single_instructor(self) -> None:
        result = _parse_instructors("Munson,Rosaria (rmunson1@swarthmore.edu)")
        assert result == ["Munson,Rosaria"]

    def test_two_instructors(self) -> None:
        result = _parse_instructors(
            "Carone,Dawn (dcarone1@swarthmore.edu),"
            "Norman,Jeffrey (jnorman1@swarthmore.edu)"
        )
        assert result == ["Carone,Dawn", "Norman,Jeffrey"]

    def test_blank_instructor(self) -> None:
        assert _parse_instructors("") == []

    def test_whitespace_only(self) -> None:
        assert _parse_instructors("  ") == []

    def test_name_with_comma_preserved(self) -> None:
        # "Last,First" — the comma is part of the name, not a separator
        result = _parse_instructors("Van Aken,Thomas (tvanake1@swarthmore.edu)")
        assert result == ["Van Aken,Thomas"]

    def test_ordering_preserved(self) -> None:
        result = _parse_instructors(
            "Alpha,Person (a@swarthmore.edu),"
            "Beta,Person (b@swarthmore.edu),"
            "Gamma,Person (c@swarthmore.edu)"
        )
        assert result == ["Alpha,Person", "Beta,Person", "Gamma,Person"]


# ---------------------------------------------------------------------------
# Linking: top-level result structure
# ---------------------------------------------------------------------------


class TestLinking:
    def test_top_level_contains_only_parents(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        child_types = {"Lab", "Drill", "Attachment", "Seminar2", "Language Section"}
        for s in sections:
            assert s.course_type not in child_types, (
                f"Child section ref_no={s.ref_no!r} type={s.course_type!r} "
                "appeared at top level"
            )

    def test_lab_nested_under_parent(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        cpsc031 = next(s for s in sections if s.ref_no == "18511")
        assert len(cpsc031.linked_sections) == 1
        assert cpsc031.linked_sections[0].ref_no == "18512"
        assert cpsc031.linked_sections[0].course_type == "Lab"

    def test_multiple_labs_nested_under_parent(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        cpsc063 = next(s for s in sections if s.ref_no == "30016")
        assert len(cpsc063.linked_sections) == 2
        refs = {s.ref_no for s in cpsc063.linked_sections}
        assert refs == {"30017", "30374"}

    def test_drill_nested_under_language_course(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        arab001 = next(s for s in sections if s.ref_no == "13503")
        assert len(arab001.linked_sections) == 1
        assert arab001.linked_sections[0].course_type == "Drill"

    def test_seminar2_nested_under_seminar1(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        anth122 = next(s for s in sections if s.ref_no == "30456")
        assert len(anth122.linked_sections) == 1
        assert anth122.linked_sections[0].course_type == "Seminar2"
        assert anth122.linked_sections[0].ref_no == "30457"

    def test_children_have_empty_linked_sections(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        for parent in sections:
            for child in parent.linked_sections:
                assert child.linked_sections == [], (
                    f"Child ref_no={child.ref_no!r} has non-empty linked_sections"
                )

    def test_child_source_order_preserved(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        cpsc063 = next(s for s in sections if s.ref_no == "30016")
        # Lab A (30017) appears before Lab B (30374) in source
        child_refs = [c.ref_no for c in cpsc063.linked_sections]
        assert child_refs == ["30017", "30374"]

    def test_duplicate_ref_no_raises(self) -> None:
        csv_content = (
            '"Fall 2026","header"\n'
            "Crs Ref No,Subj,Num,Sec,Course Title,Cr,Dist,Enr Lim,"
            "Instructor(s),Course Type,Days,Times,Bldg & Room,note\n"
            '30395,ANCH,"=""042""","=""01""",Title,1,SS,20,,Course,MWF,10:30am-11:20am,Room,\n'
            '30395,ANCH,"=""042""","=""02""",Title,1,SS,20,,Course,MWF,10:30am-11:20am,Room,\n'
        )
        with pytest.raises(CatalogParseError, match="duplicate ref_no"):
            parse_catalog(StringIO(csv_content))

    def test_orphan_child_warned_not_in_output(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        csv_content = (
            '"Fall 2026","header"\n'
            "Crs Ref No,Subj,Num,Sec,Course Title,Cr,Dist,Enr Lim,"
            "Instructor(s),Course Type,Days,Times,Bldg & Room,note\n"
            '88888,ORPH,"=""001""",A,Orphan Lab,,,18,,Lab,W,10:30am-12:00pm,Room,\n'
        )
        with caplog.at_level(logging.WARNING, logger="app.parsers.catalog"):
            result = parse_catalog(StringIO(csv_content))
        assert result == []
        assert any("Orphan" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Field normalisation via parse_catalog
# ---------------------------------------------------------------------------


class TestFieldNormalisation:
    def _get(self, ref: str) -> object:
        sections = parse_catalog(SAMPLE_CSV)
        all_sections = sections + [
            child for s in sections for child in s.linked_sections
        ]
        return next(s for s in all_sections if s.ref_no == ref)

    def test_excel_number_stripped(self) -> None:
        s = self._get("30395")
        assert s.number == "042"  # type: ignore[union-attr]

    def test_excel_section_stripped(self) -> None:
        s = self._get("30395")
        assert s.section_id == "01"  # type: ignore[union-attr]

    def test_alpha_section_unchanged(self) -> None:
        s = self._get("18512")  # Lab A — section is just "A"
        assert s.section_id == "A"  # type: ignore[union-attr]

    def test_blank_credits_are_zero_decimal(self) -> None:
        s = self._get("18512")  # Lab has blank credits
        assert s.credits == Decimal("0")  # type: ignore[union-attr]
        assert isinstance(s.credits, Decimal)  # type: ignore[union-attr]

    def test_non_blank_credits_are_decimal(self) -> None:
        s = self._get("13503")  # ARAB 001, 1.5 credits
        assert s.credits == Decimal("1.5")  # type: ignore[union-attr]
        assert isinstance(s.credits, Decimal)  # type: ignore[union-attr]

    def test_blank_enr_limit_is_none(self) -> None:
        s = self._get("19330")  # ENVS 120 has blank enr limit
        assert s.enr_limit is None  # type: ignore[union-attr]

    def test_blank_instructor_is_empty_list(self) -> None:
        s = self._get("13505")  # ARAB 001 Drill has blank instructor
        assert s.instructors == []  # type: ignore[union-attr]

    def test_distribution_is_frozenset(self) -> None:
        s = self._get("30395")
        assert isinstance(s.distribution, frozenset)  # type: ignore[union-attr]

    def test_huw_derived_in_distribution(self) -> None:
        s = self._get("19859")  # ARTH 001N: HU, W → HUW
        assert "HUW" in s.distribution  # type: ignore[union-attr]
        assert "HU" in s.distribution  # type: ignore[union-attr]
        assert "W" in s.distribution  # type: ignore[union-attr]

    def test_nsw_derived_ns_nsep_w(self) -> None:
        s = self._get("99999")  # TSTU 099: NS, NSEP, W → NSW
        assert "NSW" in s.distribution  # type: ignore[union-attr]
        assert "NSEP" in s.distribution  # type: ignore[union-attr]

    def test_multi_meeting_deduplicated(self) -> None:
        s = self._get("30341")  # BIOL 019 Lab A: M,M same time → 1 MeetingTime
        assert len(s.meeting_times) == 1  # type: ignore[union-attr]

    def test_multi_meeting_correct_time(self) -> None:
        s = self._get("30341")
        mt = s.meeting_times[0]  # type: ignore[union-attr]
        assert mt.days == ("M",)
        assert mt.start == time(13, 15)
        assert mt.end == time(16, 15)


# ---------------------------------------------------------------------------
# IO[str] interface
# ---------------------------------------------------------------------------


class TestStreamInterface:
    def test_accepts_string_stream(self) -> None:
        text = SAMPLE_CSV.read_text()
        sections = parse_catalog(StringIO(text))
        assert len(sections) == 10

    def test_path_and_stream_give_same_result(self) -> None:
        from_path = parse_catalog(SAMPLE_CSV)
        from_stream = parse_catalog(StringIO(SAMPLE_CSV.read_text()))
        assert [s.ref_no for s in from_path] == [s.ref_no for s in from_stream]


# ---------------------------------------------------------------------------
# Golden file integration test
# ---------------------------------------------------------------------------


def _section_to_dict(s: object) -> dict:  # type: ignore[type-arg]
    from app.models import CourseSection

    assert isinstance(s, CourseSection)
    return {
        "ref_no": s.ref_no,
        "subject": s.subject,
        "number": s.number,
        "section_id": s.section_id,
        "title": s.title,
        "credits": str(s.credits),
        "distribution": sorted(s.distribution),
        "enr_limit": s.enr_limit,
        "instructors": s.instructors,
        "course_type": s.course_type,
        "meeting_times": [
            {
                "days": list(mt.days),
                "start": mt.start.strftime("%H:%M:%S"),
                "end": mt.end.strftime("%H:%M:%S"),
            }
            for mt in s.meeting_times
        ],
        "note": s.note,
        "linked_sections": [_section_to_dict(c) for c in s.linked_sections],
    }


class TestGoldenFile:
    def test_parse_catalog_matches_expected_json(self) -> None:
        sections = parse_catalog(SAMPLE_CSV)
        actual = [_section_to_dict(s) for s in sections]

        with SAMPLE_EXPECTED.open() as f:
            expected = json.load(f)

        assert actual == expected
