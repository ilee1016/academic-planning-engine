"""Parse the Swarthmore course catalog CSV into CourseSection domain objects.

CSV structure:
  Row 1: metadata (skipped)
  Row 2: column headers
  Remaining rows: one section per row

Public interface:
  parse_catalog(source) -> list[CourseSection]

The returned list contains only parent sections (is_parent == True).
Child sections (Lab, Drill, Attachment, Seminar2, Language Section) are
embedded in parent.linked_sections. All other types are parsed and dropped
with a logged warning if they have no parent.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import time
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import IO

from app.models import CourseSection, MeetingTime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHILD_TYPES: frozenset[str] = frozenset(
    {"Lab", "Drill", "Attachment", "Seminar2", "Language Section"}
)

_EXCEL_RE = re.compile(r'^="(.*)"$')

# Time pattern: 1 or 2 digit hour, colon, 2 digit minute, am/pm
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(am|pm)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class CatalogParseError(ValueError):
    """Raised for unrecoverable row-level parse failures."""


# ---------------------------------------------------------------------------
# Private field helpers
# ---------------------------------------------------------------------------


def _strip_excel_formula(value: str) -> str:
    """Remove Excel formula wrapper.  =\"035\" → \"035\".  \"A\" → \"A\"."""
    m = _EXCEL_RE.match(value)
    return m.group(1) if m else value


def _parse_decimal(value: str, ref_no: str) -> Decimal:
    """Return Decimal credit value.  Blank → Decimal(\"0\").  Never uses float."""
    stripped = value.strip()
    if not stripped:
        return Decimal("0")
    try:
        return Decimal(stripped)
    except InvalidOperation:
        raise CatalogParseError(
            f"ref_no={ref_no!r}: invalid credit value {value!r}"
        )


def _parse_distribution(value: str) -> frozenset[str]:
    """Split distribution codes and add derived composites.

    Derivation rules (applied once at parse time, never recomputed downstream):
      HU + W  → HUW
      NS + W  → NSW
      SS + W  → SSW
      W alone (no HU, NS, SS) → NDW
    """
    stripped = value.strip()
    if not stripped:
        return frozenset()

    raw: set[str] = {code.strip() for code in stripped.split(",")}

    has_w = "W" in raw
    has_hu = "HU" in raw
    has_ns = "NS" in raw
    has_ss = "SS" in raw

    derived: set[str] = set()
    if has_w:
        if has_hu:
            derived.add("HUW")
        if has_ns:
            derived.add("NSW")
        if has_ss:
            derived.add("SSW")
        if not (has_hu or has_ns or has_ss):
            derived.add("NDW")

    return frozenset(raw | derived)


def _parse_time(raw: str) -> time:
    """Parse a 12-hour time string into datetime.time.

    Accepts: \"10:30am\", \"1:15pm\", \"12:00pm\" (noon), \"12:00am\" (midnight).
    Raises CatalogParseError on unrecognised format.
    """
    m = _TIME_RE.match(raw.strip())
    if not m:
        raise CatalogParseError(f"Cannot parse time {raw!r}")
    hour = int(m.group(1))
    minute = int(m.group(2))
    period = m.group(3).lower()

    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0

    return time(hour, minute)


def _parse_meeting_times(
    days_field: str, times_field: str, ref_no: str
) -> list[MeetingTime]:
    """Convert Days and Times CSV fields into a deduplicated list of MeetingTime.

    Days  = \"MWF\"          → one MeetingTime, days=(\"M\",\"W\",\"F\")
    Days  = \"M,M\"          → two comma-separated entries, paired with Times
    Times = \"10:30am-11:20am\"
    Times = \"01:15pm-04:15pm,01:15pm-04:15pm\"

    Identical MeetingTime values are deduplicated (preserving first occurrence).
    """
    days_stripped = days_field.strip()
    times_stripped = times_field.strip()

    if not days_stripped and not times_stripped:
        return []

    if not days_stripped or not times_stripped:
        raise CatalogParseError(
            f"ref_no={ref_no!r}: exactly one of Days/Times is empty "
            f"(Days={days_field!r}, Times={times_field!r})"
        )

    day_groups = [d.strip() for d in days_stripped.split(",")]
    time_ranges = [t.strip() for t in times_stripped.split(",")]

    if len(day_groups) != len(time_ranges):
        raise CatalogParseError(
            f"ref_no={ref_no!r}: {len(day_groups)} day group(s) but "
            f"{len(time_ranges)} time range(s)"
        )

    seen: set[MeetingTime] = set()
    result: list[MeetingTime] = []

    for days_str, time_str in zip(day_groups, time_ranges):
        days = tuple(days_str)  # "MWF" → ("M", "W", "F")

        # Split time range on "-" — works because time values don't contain "-"
        dash_idx = time_str.find("-")
        if dash_idx == -1:
            raise CatalogParseError(
                f"ref_no={ref_no!r}: malformed time range {time_str!r}"
            )
        start_str = time_str[:dash_idx]
        end_str = time_str[dash_idx + 1 :]

        try:
            start = _parse_time(start_str)
            end = _parse_time(end_str)
        except CatalogParseError as exc:
            raise CatalogParseError(
                f"ref_no={ref_no!r}: in time range {time_str!r}: {exc}"
            ) from exc

        if start >= end:
            raise CatalogParseError(
                f"ref_no={ref_no!r}: start {start} >= end {end} in range {time_str!r}"
            )

        mt = MeetingTime(days=days, start=start, end=end)
        if mt not in seen:
            seen.add(mt)
            result.append(mt)

    return result


def _parse_instructors(value: str) -> list[str]:
    """Return display names from \"Last,First (email), ...\" instructor field.

    Each instructor record is delimited by a closing parenthesis.  The email
    address inside parentheses is discarded; only the name portion is kept.
    Commas inside names are preserved; only the separator comma between
    distinct instructor records (after the closing paren) is ignored.
    """
    if not value.strip():
        return []

    # Each chunk is "Name (email)" — re.findall returns the name portion
    chunks = re.findall(r"([^(]+)\([^)]*\)", value)
    result: list[str] = []
    for i, chunk in enumerate(chunks):
        name = chunk.strip()
        if i > 0:
            # The previous record's closing paren left a leading ", "
            name = name.lstrip(",").strip()
        if name:
            result.append(name)
    return result


def _parse_optional_int(value: str, ref_no: str) -> int | None:
    """Parse enrollment limit: blank → None, valid int → int."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        raise CatalogParseError(
            f"ref_no={ref_no!r}: invalid enrollment limit {value!r}"
        )


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


def _parse_row(row: dict[str, str], row_num: int) -> CourseSection:
    """Convert one CSV dict row into a CourseSection.

    Raises CatalogParseError for any unrecoverable field-level failure.
    The returned section always has linked_sections=[] — the linking pass
    populates that field afterward.
    """
    ref_no = row.get("Crs Ref No", "").strip()
    if not ref_no:
        raise CatalogParseError(f"row {row_num}: missing Crs Ref No")

    subject = row.get("Subj", "").strip()
    if not subject:
        raise CatalogParseError(f"row {row_num} ref_no={ref_no!r}: missing Subj")

    raw_num = row.get("Num", "").strip()
    number = _strip_excel_formula(raw_num)
    if not number:
        raise CatalogParseError(f"row {row_num} ref_no={ref_no!r}: missing Num")

    raw_sec = row.get("Sec", "").strip()
    section_id = _strip_excel_formula(raw_sec)

    title = row.get("Course Title", "").strip()
    course_type = row.get("Course Type", "").strip()
    if not course_type:
        raise CatalogParseError(
            f"row {row_num} ref_no={ref_no!r}: missing Course Type"
        )

    credits = _parse_decimal(row.get("Cr", ""), ref_no)
    distribution = _parse_distribution(row.get("Dist", ""))
    enr_limit = _parse_optional_int(row.get("Enr Lim", ""), ref_no)
    instructors = _parse_instructors(row.get("Instructor(s)", ""))
    meeting_times = _parse_meeting_times(
        row.get("Days", ""), row.get("Times", ""), ref_no
    )
    note = row.get("note", "").strip()

    return CourseSection(
        ref_no=ref_no,
        subject=subject,
        number=number,
        section_id=section_id,
        title=title,
        credits=credits,
        distribution=distribution,
        enr_limit=enr_limit,
        instructors=instructors,
        course_type=course_type,
        meeting_times=meeting_times,
        note=note,
        linked_sections=[],
    )


# ---------------------------------------------------------------------------
# Linking pass
# ---------------------------------------------------------------------------


def _link_sections(sections: list[CourseSection]) -> list[CourseSection]:
    """Group sections by (subject, number); attach children to parents.

    Returns only parent sections (is_parent == True) with their linked children
    embedded in linked_sections.  Child sections are never present in the
    top-level result.

    Behaviour for edge cases:
    - An orphan child (no parent in its (subject, number) group) is logged as a
      warning and excluded from output.
    - Sections whose course_type is neither a parent nor a recognised child type
      are also excluded from output with a warning.
    - Each child's linked_sections remains [] (INV-10).
    """
    from collections import defaultdict

    # Group all sections by (subject, number) preserving source order
    groups: dict[tuple[str, str], list[CourseSection]] = defaultdict(list)
    for section in sections:
        groups[(section.subject, section.number)].append(section)

    top_level: list[CourseSection] = []

    for (subj, num), group in groups.items():
        parents = [s for s in group if s.is_parent]
        children = [s for s in group if s.course_type in _CHILD_TYPES]
        others = [
            s
            for s in group
            if not s.is_parent and s.course_type not in _CHILD_TYPES
        ]

        for s in others:
            logger.warning(
                "Skipping section ref_no=%r (%s %s, type=%r): "
                "not a parent and not a recognised child type",
                s.ref_no,
                subj,
                num,
                s.course_type,
            )

        if children and not parents:
            for child in children:
                logger.warning(
                    "Orphan child section ref_no=%r (%s %s, type=%r): "
                    "no parent found in same course group",
                    child.ref_no,
                    subj,
                    num,
                    child.course_type,
                )
            continue

        for parent in parents:
            # Build a new CourseSection with linked_sections populated.
            # dataclasses are mutable here, so direct assignment is safe.
            parent.linked_sections = list(children)
            top_level.append(parent)

    return top_level


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def parse_catalog(source: Path | IO[str]) -> list[CourseSection]:
    """Parse the Swarthmore course catalog CSV into parent CourseSection objects.

    Accepts either a filesystem Path or an already-open text stream (IO[str]).
    The first row of the file is a metadata row and is skipped.  The second
    row contains column headers.  All remaining rows are section records.

    Returns only parent sections (is_parent == True) in source-file order.
    Child sections are embedded in parent.linked_sections.

    Raises:
        CatalogParseError: for any unrecoverable parsing failure.
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8-sig")
        stream: IO[str] = StringIO(text)
    else:
        stream = source

    reader_iter = iter(stream)

    # Skip metadata row (row 1)
    try:
        next(reader_iter)
    except StopIteration:
        return []

    # Collect remaining lines and parse with DictReader
    remaining = list(reader_iter)
    reader = csv.DictReader(remaining)

    # Normalise header whitespace (defensive, headers are clean in practice)
    if reader.fieldnames:
        reader.fieldnames = [
            f.strip() if f is not None else f for f in reader.fieldnames
        ]

    seen_refs: set[str] = set()
    all_sections: list[CourseSection] = []

    for row_num, row in enumerate(reader, start=3):  # rows start at 3 (1=meta, 2=header)
        section = _parse_row(row, row_num)

        if section.ref_no in seen_refs:
            raise CatalogParseError(
                f"row {row_num}: duplicate ref_no {section.ref_no!r}"
            )
        seen_refs.add(section.ref_no)
        all_sections.append(section)

    return _link_sections(all_sections)
