"""Parse a Swarthmore Degree Works audit PDF into a StudentRecord.

Public interface:
    parse_audit(source: Path | IO[bytes]) -> StudentRecord

The two-function split is mandatory:
    parse_audit()        — PDF I/O only; delegates to _parse_audit_text()
    _parse_audit_text()  — all parsing logic; testable without a PDF file

Text extraction preserves line boundaries because course rows and requirement
block boundaries depend on them.  Do not lowercase the full text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import IOBase
from pathlib import Path
from typing import IO

from app.models import CompletedCourse, RequirementBlock, StudentRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class AuditParseError(ValueError):
    """Raised for unrecoverable audit parse failures.

    Messages must never include the student's name, ID, or full extracted text.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Known top-level section boundaries (matched as full stripped lines)
_SECTION_HEADERS: frozenset[str] = frozenset({
    "Distribution Requirements",
    "Distribution Requirement - Writing",
    "20 Credits Outside Major",
    "Additional Courses",
    "Courses Not Applied (INCs, NCs, Ws and Repeats)",
    "Courses Not Applied",
    "Preregistered",
    "Exceptions",
})

# Known block header patterns for requirement block detection
# These are lines immediately followed by "COMPLETE" or "INCOMPLETE"
_BLOCK_NAMES: frozenset[str] = frozenset({
    "Degree in Bachelor of Arts",
    "Distribution Requirements",
    "Distribution Requirement - Writing",
    "20 Credits Outside Major",
})

# Lines that never represent course rows — skip before attempting parse
_SKIP_LINE_PREFIXES: tuple[str, ...] = (
    "Swarthmore College",
    "Course Title",
    "Credits:",
    "Satisfied by:",
    "Still needed:",
    "Note:",
    "Type Description",
    "Legend",
    "Complete Not complete",
    "Complete (with",
    "Disclaimer",
    "Ellucian",
    "You are encouraged",
    "academic transcript",
    "www.swarthmore",
    "Degree progress",
    "Requirements Credits",
    "86%",
    "Please consult",
    "program and",
    "audit is not yet",
    "Honors students",
    "Of the Computer",
    "as those taken",
    "While CR grades",
    "Substitute with",
    "Exception by:",
    "NOTE:",
)

# Valid grades including letter grades, CR/NC, special, and AP scores
_VALID_GRADES: frozenset[str] = frozenset({
    "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-",
    "F", "CR", "NC", "S", "U", "W", "NR", "----",
    "0", "1", "2", "3", "4", "5",  # AP scores and PE unit counts
})

_GRADES_BY_LENGTH: list[str] = sorted(_VALID_GRADES, key=len, reverse=True)

# Matches (Fall|Spring|Summer) YYYY
_TERM_RE = re.compile(r"((?:Fall|Spring|Summer)\s+\d{4})")
# Matches credit field: 1, 0.5, (1), (0), (0.5)
_CREDIT_FIELD_RE = re.compile(r"\(?\d+(?:\.\d+)?\)?")
# Matches course code: SUBJ NNN[L]  (2-5 caps, space, 3 digits + optional letter)
_COURSE_CODE_RE = re.compile(r"([A-Z]{2,5})\s+(\d{3}[A-Z]?\d?)\b")
# Matches "Exempted from SUBJ NNN via" (with flexible spacing)
_EXEMPTION_RE = re.compile(r"Exempted from ([A-Z]{2,5})\s+(\d{3}[A-Z]?\d?) via", re.IGNORECASE)
# Matches audit date: MM/DD/YYYY
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")


# ---------------------------------------------------------------------------
# Private dataclasses (module-internal only)
# ---------------------------------------------------------------------------


@dataclass
class _AuditHeader:
    name: str
    student_id: str
    major: str
    class_year: int
    catalog_year: str
    credits_required: Decimal
    credits_applied: Decimal
    audit_date: date


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize whitespace artifacts without losing line structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Replace non-breaking spaces and similar whitespace with regular space
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    # Strip trailing spaces per line
    lines = [line.rstrip() for line in text.split("\n")]
    # Collapse runs of more than two consecutive blank lines
    result: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                result.append(line)
        else:
            blank_run = 0
            result.append(line)
    return "\n".join(result)


def _strip_page_headers(lines: list[str]) -> list[str]:
    """Remove Degree Works running page headers that repeat on every page.

    Typical form: 'Swarthmore College LastName, FirstName - XXXXXXXXX'
    Also remove the standalone 'Swarthmore College' line that follows.
    """
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Remove page-top header: "Swarthmore College Name - ID"
        if re.match(r"^Swarthmore College .+ - \d{9}$", stripped):
            continue
        # Remove lone "Swarthmore College" lines (second header line on some pages)
        if stripped == "Swarthmore College":
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def _parse_header(text: str) -> _AuditHeader:
    """Extract student header fields from the full audit text."""

    def _require(pattern: str, label: str) -> re.Match[str]:
        m = re.search(pattern, text)
        if not m:
            raise AuditParseError(f"Missing required audit field: {label}.")
        return m

    # Name: "Student name Last, First"
    name_m = _require(r"Student name\s+(.+?)(?:\n|$)", "student name")
    name = name_m.group(1).strip()

    # Student ID
    id_m = _require(r"Student ID\s+(\d+)", "student ID")
    student_id = id_m.group(1).strip()

    # Major: "Major X Advisor" or "Major X\n"
    major_m = _require(r"Major\s+(.+?)\s+Advisor", "major")
    major = major_m.group(1).strip()

    # Class year
    class_year_m = _require(r"Class Year\s+(\d{4})", "class year")
    try:
        class_year = int(class_year_m.group(1))
    except ValueError:
        raise AuditParseError(f"Invalid class year: {class_year_m.group(1)!r}")

    # Catalog year
    catalog_m = _require(r"Catalog Year\s+(\d+)", "catalog year")
    catalog_year = catalog_m.group(1).strip()

    # Audit date: "Audit date MM/DD/YYYY"
    date_m = _require(r"Audit date\s+(\d{2}/\d{2}/\d{4})", "audit date")
    try:
        audit_date = datetime.strptime(date_m.group(1), "%m/%d/%Y").date()
    except ValueError:
        raise AuditParseError(f"Invalid audit date format: {date_m.group(1)!r}")

    # Credits: "Credits required: N Credits applied: M"
    credits_m = _require(
        r"Credits required:\s*([\d.]+)\s+Credits applied:\s*([\d.]+)",
        "credit totals",
    )
    try:
        credits_required = Decimal(credits_m.group(1))
        credits_applied = Decimal(credits_m.group(2))
    except InvalidOperation:
        raise AuditParseError("Invalid credit value in audit header.")

    return _AuditHeader(
        name=name,
        student_id=student_id,
        major=major,
        class_year=class_year,
        catalog_year=catalog_year,
        credits_required=credits_required,
        credits_applied=credits_applied,
        audit_date=audit_date,
    )


# ---------------------------------------------------------------------------
# Credit parsing
# ---------------------------------------------------------------------------


def _parse_credit(raw: str) -> Decimal:
    """Parse credit string to Decimal.  '(1)' → Decimal('1'), '0.5' → Decimal('0.5')."""
    stripped = raw.strip().strip("()")
    if not stripped:
        return Decimal("0")
    try:
        return Decimal(stripped)
    except InvalidOperation:
        raise AuditParseError(f"Invalid credit value in course row: {raw!r}")


# ---------------------------------------------------------------------------
# Course row parsing
# ---------------------------------------------------------------------------


def _try_parse_course_line(line: str) -> CompletedCourse | None:
    """Try to parse a single text line as a course row.

    Parses right-to-left: term → credit → grade → course code + title.
    Returns None if the line is not a recognizable course row.

    Handles:
    - Normal rows: 'CPSC 031 Intro to Computer Systems CR 1 Fall 2023'
    - Parenthesized credits: 'CPSC 063 AI ---- (1) Fall 2026'
    - Rows with block-name prefixes: 'Group 1 CPSC 041 Algorithms B+ 1 Spring 2025'
    """
    stripped = line.strip()

    # Quick reject: lines starting with known non-course prefixes
    for prefix in _SKIP_LINE_PREFIXES:
        if stripped.startswith(prefix):
            return None

    # Skip sub-rows: AP credit notes, exemption notes, exception notes
    if "NO TRANSCRIPT DETAI" in stripped:
        return None
    if stripped.startswith("Exempted from"):
        return None
    if stripped.startswith("Exception by:"):
        return None

    # --- Step 1: Find the LAST term in the line ---
    term_match: re.Match[str] | None = None
    for m in _TERM_RE.finditer(stripped):
        term_match = m
    if term_match is None:
        return None
    term = term_match.group(1)
    rest = stripped[: term_match.start()].rstrip()

    # --- Step 2: Find credit immediately before term ---
    # Credit is the last token; may be parenthesized
    credit_m = re.search(r"(\(?\d+(?:\.\d+)?\)?)\s*$", rest)
    if credit_m is None:
        return None
    raw_credit = credit_m.group(1)
    try:
        credits = _parse_credit(raw_credit)
    except AuditParseError:
        return None
    rest = rest[: credit_m.start()].rstrip()

    # --- Step 3: Find grade before credit (fixed set, longest match first) ---
    grade: str | None = None
    for g in _GRADES_BY_LENGTH:
        if rest == g or rest.endswith(" " + g):
            grade = g
            rest = rest[: len(rest) - len(g)].rstrip()
            break
    if grade is None:
        return None

    # --- Step 4: Find course code (SUBJ NNN) ---
    code_m = _COURSE_CODE_RE.search(rest)
    if code_m is None:
        return None
    subject = code_m.group(1)
    number = code_m.group(2)
    code = f"{subject} {number}"
    title = rest[code_m.end() :].strip()

    return CompletedCourse(
        code=code,
        title=title,
        grade=grade,
        credits=credits,
        term=term,
    )


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------


def _find_section_positions(lines: list[str]) -> dict[str, int]:
    """Return {section_name: line_index} for each known section header found."""
    positions: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Exact match against known headers
        if stripped in _SECTION_HEADERS:
            if stripped not in positions:
                positions[stripped] = i
        # "Major in ..." — flexible match for different majors
        if stripped.startswith("Major in ") and stripped not in positions:
            positions[stripped] = i
        # "Degree in Bachelor of Arts" is in _BLOCK_NAMES not _SECTION_HEADERS
        if stripped == "Degree in Bachelor of Arts" and stripped not in positions:
            positions[stripped] = i
    return positions


def _lines_for_section(
    lines: list[str],
    start: int,
    all_positions: dict[str, int],
) -> list[str]:
    """Return lines from `start` up to the next known section boundary."""
    other_starts = sorted(
        pos for name, pos in all_positions.items() if pos > start
    )
    end = other_starts[0] if other_starts else len(lines)
    return lines[start:end]


# ---------------------------------------------------------------------------
# Course row extraction from a block of lines
# ---------------------------------------------------------------------------


def _extract_courses_from_lines(
    lines: list[str],
) -> list[CompletedCourse]:
    """Parse all recognizable course rows from a list of text lines."""
    courses: list[CompletedCourse] = []
    for line in lines:
        cc = _try_parse_course_line(line)
        if cc is not None:
            courses.append(cc)
    return courses


def _dedup_courses(courses: list[CompletedCourse]) -> list[CompletedCourse]:
    """Deduplicate courses by (code, grade, credits, term, title), preserving order."""
    seen: set[tuple[str, str, str, str, str]] = set()
    out: list[CompletedCourse] = []
    for c in courses:
        key = (c.code, c.grade, str(c.credits), c.term, c.title)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Requirement block extraction
# ---------------------------------------------------------------------------


def _extract_still_needed(lines: list[str], start: int) -> str:
    """Collect 'Still needed:' text starting from `start` until a section boundary.

    Concatenates wrapped continuation lines.  Stops on section boundaries,
    course table headers, or blank-line breaks after content.
    """
    boundary_patterns = (
        "Course Title",
        "Credits:",
        "COMPLETE",
        "INCOMPLETE",
    )
    in_still_needed = False
    fragments: list[str] = []
    blank_count = 0

    for i in range(start, len(lines)):
        stripped = lines[i].strip()

        if not stripped:
            blank_count += 1
            if in_still_needed and blank_count > 1:
                break
            continue
        blank_count = 0

        if stripped in _SECTION_HEADERS or stripped.startswith("Major in "):
            break
        if any(stripped.startswith(bp) for bp in boundary_patterns):
            break

        if stripped.startswith("Still needed:"):
            in_still_needed = True
            content = stripped[len("Still needed:"):].strip()
            if content:
                fragments.append(content)
            continue

        if in_still_needed:
            fragments.append(stripped)

    return " ".join(fragments)


def _extract_requirement_blocks(lines: list[str]) -> list[RequirementBlock]:
    """Find requirement blocks: header line immediately followed by COMPLETE/INCOMPLETE.

    Also flexibly matches 'Major in ...' sections.
    """
    blocks: list[RequirementBlock] = []
    seen_names: set[str] = set()
    n = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Check if one of the next two non-empty lines is COMPLETE/INCOMPLETE
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j >= n:
            continue

        next_stripped = lines[j].strip()
        if next_stripped not in ("COMPLETE", "INCOMPLETE"):
            continue

        # `stripped` is the block name
        name = stripped
        if name in seen_names:
            continue

        # Only accept known block names or "Major in ..." patterns
        is_known = name in _BLOCK_NAMES
        is_major = name.startswith("Major in ")
        if not (is_known or is_major):
            continue

        seen_names.add(name)
        status = next_stripped

        still_needed_text = ""
        if status == "INCOMPLETE":
            still_needed_text = _extract_still_needed(lines, j + 1)

        blocks.append(
            RequirementBlock(
                name=name,
                status=status,
                still_needed_text=still_needed_text,
            )
        )

    return blocks


# ---------------------------------------------------------------------------
# Exemption and exception extraction
# ---------------------------------------------------------------------------


def _parse_exemptions(text: str) -> list[str]:
    """Extract course codes from 'Exempted from SUBJ NNN via ...' notes."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _EXEMPTION_RE.finditer(text):
        code = f"{m.group(1).upper()} {m.group(2)}"
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def _parse_exceptions(lines: list[str], exceptions_start: int) -> list[str]:
    """Extract exception descriptions from the Exceptions table.

    The table has a header row followed by data rows of the form:
        TypeLabel Description Date Creator Block Applied
    Extract the description column by splitting on the date pattern.
    """
    # Skip the header line "Type Description Created on Created by Block Enforced"
    _EXCEPTIONS_HEADER = "Type Description Created on Created by Block Enforced"
    _KNOWN_TYPES = (
        "Apply Here",
        "Exception",
        "Waiver",
        "Substitution",
        "Grade Replacement",
        "Credit Adjustment",
        "Override",
    )
    result: list[str] = []
    in_table = False

    for line in lines[exceptions_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == _EXCEPTIONS_HEADER:
            in_table = True
            continue
        # Stop at the legend or other terminal sections
        if stripped == "Legend" or stripped.startswith("Complete Not"):
            break

        if not in_table:
            continue

        # Split on the first date (MM/DD/YYYY) to isolate description
        date_m = _DATE_RE.search(stripped)
        if date_m:
            before_date = stripped[: date_m.start()].strip()
            # Strip known type labels from the start of the description
            for type_label in _KNOWN_TYPES:
                if before_date.startswith(type_label):
                    before_date = before_date[len(type_label) :].strip()
                    break
            if before_date:
                result.append(before_date)

    return result


# ---------------------------------------------------------------------------
# Main parse orchestrator
# ---------------------------------------------------------------------------


def _parse_audit_text(text: str) -> StudentRecord:
    """Parse extracted Degree Works text into a StudentRecord.

    This function contains all parsing logic.  It does not open files or
    extract PDF text; use parse_audit() for that.

    Raises AuditParseError for unrecoverable failures.
    """
    if not text or not text.strip():
        raise AuditParseError("Could not extract text from Degree Works PDF.")

    # Verify this looks like a Degree Works audit
    if "Student ID" not in text or "Audit date" not in text:
        raise AuditParseError(
            "File does not appear to be a Swarthmore Degree Works audit."
        )

    normalized = _normalize_text(text)
    lines = _strip_page_headers(normalized.split("\n"))

    # --- Parse header ---
    full_text = "\n".join(lines)
    header = _parse_header(full_text)

    # --- Find section positions ---
    section_positions = _find_section_positions(lines)

    # --- Determine section ranges ---
    def _get_lines(name: str) -> list[str]:
        """Return lines for a named section, or [] if not found."""
        if name not in section_positions:
            return []
        start = section_positions[name]
        return _lines_for_section(lines, start, section_positions)

    # Locate Additional / Not Applied / Preregistered / Exceptions starts
    additional_start = section_positions.get("Additional Courses")
    not_applied_start = section_positions.get(
        "Courses Not Applied (INCs, NCs, Ws and Repeats)",
        section_positions.get("Courses Not Applied"),
    )
    preregistered_start = section_positions.get("Preregistered")
    exceptions_start = section_positions.get("Exceptions")

    # Boundary: line index where "additional" sections start (everything
    # after this is excluded from completed_courses)
    additional_boundary = min(
        pos
        for pos in [
            additional_start,
            not_applied_start,
            preregistered_start,
            exceptions_start,
            len(lines),
        ]
        if pos is not None
    )

    # --- Extract completed courses ---
    # From all lines before the additional-section boundary
    completed_raw = _extract_courses_from_lines(lines[:additional_boundary])
    completed_courses = _dedup_courses(completed_raw)

    # --- Extract other_courses (Additional + Not Applied) ---
    other_raw: list[CompletedCourse] = []
    if additional_start is not None:
        other_raw.extend(_extract_courses_from_lines(_get_lines("Additional Courses")))
    if not_applied_start is not None:
        key = (
            "Courses Not Applied (INCs, NCs, Ws and Repeats)"
            if "Courses Not Applied (INCs, NCs, Ws and Repeats)" in section_positions
            else "Courses Not Applied"
        )
        other_raw.extend(_extract_courses_from_lines(_get_lines(key)))

    # Deduplicate and remove any already in completed_courses
    completed_keys: set[tuple[str, str, str, str]] = {
        (c.code, c.grade, str(c.credits), c.term) for c in completed_courses
    }
    other_courses = _dedup_courses(
        [
            c
            for c in other_raw
            if (c.code, c.grade, str(c.credits), c.term) not in completed_keys
        ]
    )

    # --- Extract preregistered courses ---
    preregistered_courses: list[CompletedCourse] = []
    if preregistered_start is not None:
        preregistered_courses = _dedup_courses(
            _extract_courses_from_lines(_get_lines("Preregistered"))
        )

    # --- Extract requirement blocks ---
    requirement_blocks = _extract_requirement_blocks(lines)
    if not requirement_blocks:
        raise AuditParseError("No recognizable requirement blocks found.")
    logger.debug("Parsed %d requirement blocks.", len(requirement_blocks))

    # --- Extract exemptions ---
    exempted_courses = _parse_exemptions(full_text)

    # --- Extract exceptions ---
    exceptions: list[str] = []
    if exceptions_start is not None:
        exceptions = _parse_exceptions(lines, exceptions_start)

    return StudentRecord(
        name=header.name,
        student_id=header.student_id,
        major=header.major,
        class_year=header.class_year,
        catalog_year=header.catalog_year,
        credits_required=header.credits_required,
        credits_applied=header.credits_applied,
        audit_date=header.audit_date,
        completed_courses=completed_courses,
        preregistered_courses=preregistered_courses,
        other_courses=other_courses,
        exempted_courses=exempted_courses,
        requirement_blocks=requirement_blocks,
        exceptions=exceptions,
    )


# ---------------------------------------------------------------------------
# Public interface — PDF entry point
# ---------------------------------------------------------------------------


def parse_audit(source: Path | IO[bytes]) -> StudentRecord:
    """Parse a Swarthmore Degree Works audit PDF into a StudentRecord.

    Accepts either a filesystem Path or an already-open binary stream
    (suitable for FastAPI UploadFile.file).  The PDF is never written to disk.

    Raises AuditParseError for unrecoverable failures.
    """
    import pdfplumber  # import here to avoid top-level dependency in tests

    try:
        with pdfplumber.open(source) as pdf:  # type: ignore[arg-type]
            page_texts: list[str] = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                page_texts.append(page_text)

        text = "\n".join(page_texts)
    except AuditParseError:
        raise
    except Exception as exc:
        raise AuditParseError(
            "Could not extract text from Degree Works PDF."
        ) from exc

    if not text.strip():
        raise AuditParseError("Could not extract text from Degree Works PDF.")

    return _parse_audit_text(text)
