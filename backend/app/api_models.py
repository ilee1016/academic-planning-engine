"""Pydantic request/response models for the Academic Planning Engine API.

All domain logic belongs in core/.  These models are serialization contracts only.
Domain dataclasses (models.py) are never returned directly from API routes.

Conversion helpers at the bottom of this module bridge between the two layers.

Privacy rules:
  - Student name and student_id are never included in any response schema.
  - Raw audit text, file bytes, and exception tracebacks are never serialized.
  - Decimal credit values are serialized as strings to preserve exactness.

INV-API-07: API DTOs are distinct from core models.
INV-API-08: Decimal precision survives serialization (use str, not float).
"""
from __future__ import annotations

import hashlib
import re
from datetime import time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    ConstraintDiagnostic,
    CourseSection,
    MeetingTime,
    Preferences,
    RankedSchedule,
    RequirementItem,
    RequirementStatus,
    Schedule,
    StudentRecord,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_WEEKDAYS: frozenset[str] = frozenset({"M", "T", "W", "R", "F"})
_MAX_CREDITS_CEILING = Decimal("8")
_MAX_LIST_LEN = 20
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

# Category names supported by the API.
SCHEDULE_CATEGORIES: tuple[str, ...] = (
    "requirements_first",
    "preferred_subjects",
    "compact_schedule",
    "balanced",
    "current_registration",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_time_str(value: str) -> time:
    m = _TIME_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid time format {value!r}; expected HH:MM or H:MM")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {value!r}")
    return time(hour, minute)


def _format_time(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _schedule_id(schedule: Schedule) -> str:
    """Deterministic, identity-free schedule ID derived from section ref_nos."""
    canonical = ",".join(sorted(s.ref_no for s in schedule.all_sections))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def compute_schedule_id(schedule: Schedule) -> str:
    """Public alias for the deterministic schedule ID computation.

    Used by the explanation endpoint to match schedule_id path parameters
    against ranked schedules stored in session state.
    """
    return _schedule_id(schedule)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PreferencesRequest(BaseModel):
    """Client-supplied scheduling preferences.

    Validates input ranges and converts to the domain Preferences dataclass
    via preferences_from_request().
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    min_credits: Decimal = Decimal("3")
    max_credits: Decimal = Decimal("4")
    earliest_start: str | None = None  # "HH:MM"
    latest_end: str | None = None      # "HH:MM"
    free_days: list[str] = Field(default_factory=list)
    preferred_subjects: list[str] = Field(default_factory=list)
    excluded_courses: list[str] = Field(default_factory=list)
    lock_preregistered: bool = True

    @field_validator("min_credits", "max_credits", mode="before")
    @classmethod
    def _validate_credit_str(cls, v: object) -> Decimal:
        if isinstance(v, str):
            try:
                return Decimal(v)
            except Exception:
                raise ValueError(f"Invalid decimal credit value: {v!r}")
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, Decimal):
            return v
        raise ValueError(f"Cannot convert {type(v).__name__} to Decimal")

    @field_validator("free_days", mode="before")
    @classmethod
    def _validate_free_days(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("free_days must be a list")
        if len(v) > _MAX_LIST_LEN:
            raise ValueError(f"free_days may contain at most {_MAX_LIST_LEN} entries")
        seen: set[str] = set()
        result: list[str] = []
        for day in v:
            if not isinstance(day, str) or not day.strip():
                raise ValueError(f"Invalid day value: {day!r}")
            d = day.strip().upper()
            if d not in _VALID_WEEKDAYS:
                raise ValueError(
                    f"Unrecognised weekday {d!r}; use M, T, W, R, or F"
                )
            if d not in seen:
                seen.add(d)
                result.append(d)
        return result

    @field_validator("preferred_subjects", mode="before")
    @classmethod
    def _validate_subjects(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("preferred_subjects must be a list")
        if len(v) > _MAX_LIST_LEN:
            raise ValueError(f"preferred_subjects may contain at most {_MAX_LIST_LEN} entries")
        seen: set[str] = set()
        result: list[str] = []
        for subj in v:
            if not isinstance(subj, str) or not subj.strip():
                raise ValueError(f"Invalid subject: {subj!r}")
            s = subj.strip().upper()
            if s not in seen:
                seen.add(s)
                result.append(s)
        return result

    @field_validator("excluded_courses", mode="before")
    @classmethod
    def _validate_excluded_courses(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("excluded_courses must be a list")
        if len(v) > _MAX_LIST_LEN:
            raise ValueError(f"excluded_courses may contain at most {_MAX_LIST_LEN} entries")
        seen: set[str] = set()
        result: list[str] = []
        for code in v:
            if not isinstance(code, str) or not code.strip():
                raise ValueError(f"Invalid course code: {code!r}")
            c = code.strip().upper()
            if c not in seen:
                seen.add(c)
                result.append(c)
        return result

    @model_validator(mode="after")
    def _validate_credit_range_and_time(self) -> "PreferencesRequest":
        if self.min_credits < Decimal("0"):
            raise ValueError("min_credits must be >= 0")
        if self.max_credits < self.min_credits:
            raise ValueError("max_credits must be >= min_credits")
        if self.max_credits > _MAX_CREDITS_CEILING:
            raise ValueError(
                f"max_credits must be <= {_MAX_CREDITS_CEILING}; "
                "contact your advisor for unusual loads"
            )
        if self.earliest_start is not None:
            _parse_time_str(self.earliest_start)  # validates format
        if self.latest_end is not None:
            _parse_time_str(self.latest_end)
        if self.earliest_start is not None and self.latest_end is not None:
            es = _parse_time_str(self.earliest_start)
            le = _parse_time_str(self.latest_end)
            if es >= le:
                raise ValueError("earliest_start must be before latest_end")
        return self


class GenerateSchedulesRequest(BaseModel):
    """Request body for POST /api/session/{id}/schedules."""

    preferences: PreferencesRequest
    locked_ref_nos: list[str] = Field(default_factory=list)
    max_results: int = Field(500, ge=1, le=2000)
    max_ranked: int | None = Field(None, ge=1)


class AmbiguousChoiceSection(BaseModel):
    """One catalog section that is a candidate for a preregistered course."""

    ref_no: str
    section_id: str
    meeting_times: list["MeetingTimeResponse"]


class AmbiguousCourseError(BaseModel):
    """Structured payload returned when a preregistered course is ambiguous."""

    course_code: str
    choices: list[AmbiguousChoiceSection]


# ---------------------------------------------------------------------------
# Response models — sub-objects
# ---------------------------------------------------------------------------


class MeetingTimeResponse(BaseModel):
    """Meeting time serialization."""

    days: list[str]
    start: str   # "HH:MM"
    end: str     # "HH:MM"


class SectionResponse(BaseModel):
    """One course section for display in the API."""

    ref_no: str
    course_code: str
    section_id: str
    title: str
    credits: str        # Decimal serialized as str
    course_type: str
    instructors: list[str]
    meeting_times: list[MeetingTimeResponse]


class RequirementGainResponse(BaseModel):
    """One requirement item that a schedule would advance."""

    id: str
    label: str
    notes: str


class ScoreBreakdownResponse(BaseModel):
    """Per-component score breakdown."""

    requirement_gains: float
    preferred_subjects: float
    free_days: float
    compactness: float
    credit_load: float


class RankedScheduleResponse(BaseModel):
    """One ranked schedule for API display."""

    schedule_id: str            # deterministic, identity-free hash
    parent_sections: list[SectionResponse]
    linked_sections: list[SectionResponse]
    total_credits: str          # Decimal as str
    score: float
    score_breakdown: ScoreBreakdownResponse
    requirement_gains: list[RequirementGainResponse]
    category: str
    explanation: str


class ConstraintDiagnosticResponse(BaseModel):
    """Serialized ConstraintDiagnostic."""

    no_valid_schedules: bool
    reasons: list[str]
    suggested_relaxations: list[str]


class SearchMetadataResponse(BaseModel):
    """Metadata about the solver run."""

    candidate_count: int
    option_count: int
    generated_schedules: int
    solver_cap: int
    cap_reached: bool
    search_space_fully_enumerated: bool


# ---------------------------------------------------------------------------
# Response models — top-level
# ---------------------------------------------------------------------------


class CreateSessionResponse(BaseModel):
    """Returned by POST /api/session."""

    session_id: str
    created_at: str   # ISO-8601 UTC


class StudentSummaryResponse(BaseModel):
    """Sanitized student summary — no name or ID."""

    major: str
    class_year: int
    catalog_year: str
    credits_required: str   # Decimal as str
    credits_applied: str    # Decimal as str


class CatalogSummaryResponse(BaseModel):
    parent_sections: int


class RequirementSummaryResponse(BaseModel):
    total_items: int
    unsatisfied_items: int
    unmatched_items: list[str]   # IDs of items that matched no DW block


class InputSummaryResponse(BaseModel):
    """Returned by POST /api/session/{id}/inputs."""

    session_id: str
    student_summary: StudentSummaryResponse
    catalog_summary: CatalogSummaryResponse
    requirement_summary: RequirementSummaryResponse
    warnings: list[str]


class ScheduleResultResponse(BaseModel):
    """Returned by POST /api/session/{id}/schedules when schedules exist."""

    status: str   # "schedules_found"
    top_schedules: list[RankedScheduleResponse]
    categories: dict[str, list[RankedScheduleResponse]]
    search_metadata: SearchMetadataResponse


class DiagnosticResultResponse(BaseModel):
    """Returned by POST /api/session/{id}/schedules when no schedules exist."""

    status: str   # "no_valid_schedules"
    diagnostic: ConstraintDiagnosticResponse
    search_metadata: SearchMetadataResponse


class SessionInfoResponse(BaseModel):
    """Returned by GET /api/session/{id}."""

    session_id: str
    created_at: str
    last_accessed_at: str
    inputs_loaded: bool


class ExplanationResponse(BaseModel):
    """Returned by POST /api/session/{id}/schedules/{schedule_id}/explanation."""

    schedule_id: str
    explanation: str
    source: Literal["provider", "fallback"]


class HealthResponse(BaseModel):
    status: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Conversion: request → domain
# ---------------------------------------------------------------------------


def preferences_from_request(req: PreferencesRequest) -> Preferences:
    """Convert a validated PreferencesRequest to the domain Preferences dataclass."""
    earliest: time | None = (
        _parse_time_str(req.earliest_start) if req.earliest_start else None
    )
    latest: time | None = (
        _parse_time_str(req.latest_end) if req.latest_end else None
    )
    return Preferences(
        min_credits=req.min_credits,
        max_credits=req.max_credits,
        earliest_start=earliest,
        latest_end=latest,
        free_days=list(req.free_days),
        preferred_subjects=list(req.preferred_subjects),
        excluded_courses=list(req.excluded_courses),
        lock_preregistered=req.lock_preregistered,
    )


# ---------------------------------------------------------------------------
# Conversion: domain → response
# ---------------------------------------------------------------------------


def _mt_to_response(mt: MeetingTime) -> MeetingTimeResponse:
    return MeetingTimeResponse(
        days=list(mt.days),
        start=_format_time(mt.start),
        end=_format_time(mt.end),
    )


def _section_to_response(section: CourseSection) -> SectionResponse:
    return SectionResponse(
        ref_no=section.ref_no,
        course_code=section.course_code,
        section_id=section.section_id,
        title=section.title,
        credits=str(section.credits),
        course_type=section.course_type,
        instructors=list(section.instructors),
        meeting_times=[_mt_to_response(mt) for mt in section.meeting_times],
    )


def _requirement_gain_to_response(item: RequirementItem) -> RequirementGainResponse:
    return RequirementGainResponse(id=item.id, label=item.label, notes=item.notes)


def ranked_schedule_to_response(rs: RankedSchedule) -> RankedScheduleResponse:
    """Convert a RankedSchedule to its API response representation."""
    bd = rs.score_breakdown
    return RankedScheduleResponse(
        schedule_id=_schedule_id(rs.schedule),
        parent_sections=[_section_to_response(s) for s in rs.schedule.parent_sections],
        linked_sections=[_section_to_response(s) for s in rs.schedule.lab_sections],
        total_credits=str(rs.schedule.total_credits),
        score=rs.score,
        score_breakdown=ScoreBreakdownResponse(
            requirement_gains=bd.get("requirement_gains", 0.0),
            preferred_subjects=bd.get("preferred_subjects", 0.0),
            free_days=bd.get("free_days", 0.0),
            compactness=bd.get("compactness", 0.0),
            credit_load=bd.get("credit_load", 0.0),
        ),
        requirement_gains=[_requirement_gain_to_response(g) for g in rs.requirement_gains],
        category=rs.category,
        explanation=rs.explanation,
    )


def diagnostic_to_response(diag: ConstraintDiagnostic) -> ConstraintDiagnosticResponse:
    return ConstraintDiagnosticResponse(
        no_valid_schedules=diag.no_valid_schedules,
        reasons=list(diag.reasons),
        suggested_relaxations=list(diag.suggested_relaxations),
    )


def student_summary(student: StudentRecord) -> StudentSummaryResponse:
    """Sanitized student summary — name and student_id are deliberately excluded."""
    return StudentSummaryResponse(
        major=student.major,
        class_year=student.class_year,
        catalog_year=student.catalog_year,
        credits_required=str(student.credits_required),
        credits_applied=str(student.credits_applied),
    )


def requirement_summary(
    req_status: RequirementStatus,
    unmatched_ids: list[str],
) -> RequirementSummaryResponse:
    total = len(req_status.items)
    unsatisfied = sum(1 for i in req_status.items if not i.satisfied)
    return RequirementSummaryResponse(
        total_items=total,
        unsatisfied_items=unsatisfied,
        unmatched_items=list(unmatched_ids),
    )
