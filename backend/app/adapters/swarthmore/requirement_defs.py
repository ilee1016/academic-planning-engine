"""Swarthmore CS major requirement definitions for catalog year 202304.

Returns RequirementDefinition objects that pair each RequirementItem with the
Degree Works block name patterns used to determine its satisfied state at runtime.

Public API:
    get_requirement_definitions(student) -> list[RequirementDefinition]
    UnsupportedProgramError
"""
from __future__ import annotations

from app.models import RequirementDefinition, RequirementItem, StudentRecord


class UnsupportedProgramError(ValueError):
    """Raised when no requirement definitions exist for the student's major and catalog year."""


def _cs_202304_definitions() -> list[RequirementDefinition]:
    """Return fresh RequirementDefinition objects for CS major, catalog year 202304."""
    return [
        RequirementDefinition(
            item=RequirementItem(
                id="writing_hu_or_ns",
                label="Writing Requirement — Humanities or Natural Sciences",
                satisfied=False,
                satisfying_courses=[],
                matching_attributes=frozenset({"HUW", "NSW"}),
                notes="One course with an HUW or NSW attribute taken at Swarthmore.",
            ),
            block_patterns=(
                "Distribution Requirement - Writing",
                "Writing Requirement",
            ),
        ),
        RequirementDefinition(
            item=RequirementItem(
                id="cs_cpsc031",
                label="CS Major — Intro to Computer Systems (CPSC 031)",
                satisfied=False,
                satisfying_courses=["CPSC 031"],
                matching_attributes=frozenset(),
                notes="Minimum letter grade C required; CR does not satisfy the major requirement.",
            ),
            block_patterns=(
                "Intro to Computer Systems",
                "CPSC 031",
            ),
        ),
        RequirementDefinition(
            item=RequirementItem(
                id="cs_cpsc_credits",
                label="CS Major — Required CPSC Credits",
                satisfied=False,
                satisfying_courses=[],
                matching_attributes=frozenset(),
                notes=(
                    "Any CPSC course worth at least one academic credit contributes. "
                    "MVP limitation: this item represents the remaining CPSC-credit category; "
                    "one matching section does not imply the full credit deficit is cleared."
                ),
                subject_predicate="CPSC",
            ),
            block_patterns=(
                "8 Required Credits in CPSC",
                "Required Credits in CPSC",
            ),
        ),
        RequirementDefinition(
            item=RequirementItem(
                id="cs_senior_comp",
                label="CS Major — Senior Comprehensive (CPSC 099)",
                satisfied=False,
                satisfying_courses=["CPSC 099"],
                matching_attributes=frozenset(),
                notes=(
                    "Automatically registered for senior CS majors; "
                    "no scheduling action required."
                ),
                auto_registered=True,
            ),
            block_patterns=(
                "Senior Comprehensive",
                "CPSC 099",
            ),
        ),
    ]


def get_requirement_definitions(student: StudentRecord) -> list[RequirementDefinition]:
    """Return requirement definitions for the student's major and catalog year.

    Raises UnsupportedProgramError for unrecognized (major, catalog_year) combinations.
    Returns fresh objects on every call; callers may freely mutate the returned lists.
    """
    major = student.major.strip()
    catalog_year = student.catalog_year.strip()
    if major == "Computer Science" and catalog_year == "202304":
        return _cs_202304_definitions()
    raise UnsupportedProgramError(
        f"Unsupported requirement definition: major={major!r}, catalog_year={catalog_year!r}"
    )
