"""FastAPI application — route definitions and exception handlers.

Responsibilities of this module:
  - Declare the FastAPI app and mount routes.
  - Validate HTTP inputs (file types, sizes, session existence).
  - Translate domain errors into HTTP responses.
  - Inject the session store and explanation service dependencies.
  - Orchestrate (call orchestration.py); never run core domain logic inline.
  - Read environment variables for configuration (INV-04).

Responsibilities elsewhere:
  - api_models.py: request/response schema definitions and conversions.
  - session_store.py: in-memory session management.
  - orchestration.py: pipeline coordination.
  - core/explainer.py: sanitized DTO, fallback, validation.
  - explanation_provider.py: provider protocol + Anthropic implementation.
  - explanation_service.py: provider coordination + cache.

Privacy:
  - Uploaded file bytes are read into memory and discarded after parsing.
  - Student name and ID are never logged or returned in API responses.
  - Raw exception detail is never sent to clients (sanitized messages only).
  - Explanation provider failures are logged as sanitized category strings only.

Invariants:
  INV-04   main.py is the only module that reads environment variables.
  INV-API-01  Raw uploads never persisted.
  INV-API-03  Student identity never serialized.
  INV-API-06  Routes contain no domain logic.
  INV-API-12  Valid no-schedule results return diagnostics, not HTTP 500.
  INV-AI-08   Schedule generation never calls the explanation provider.
  INV-AI-09   Provider calls occur only through ExplanationService.
  INV-AI-11   Provider credentials never appear in API responses or OpenAPI.
"""
from __future__ import annotations

import logging
import os
from io import BytesIO, StringIO
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.adapters.swarthmore.requirement_defs import UnsupportedProgramError
from app.api_models import (
    AmbiguousChoiceSection,
    AmbiguousCourseError,
    CatalogSummaryResponse,
    CreateSessionResponse,
    DiagnosticResultResponse,
    ErrorDetail,
    ErrorEnvelope,
    ExplanationResponse,
    GenerateSchedulesRequest,
    HealthResponse,
    InputSummaryResponse,
    MeetingTimeResponse,
    ScheduleResultResponse,
    SearchMetadataResponse,
    SessionInfoResponse,
    compute_schedule_id,
    diagnostic_to_response,
    preferences_from_request,
    ranked_schedule_to_response,
    requirement_summary,
    student_summary,
)
from app.core.solver import InvalidLockedScheduleError, LinkedSectionError
from app.explanation_provider import load_provider
from app.explanation_service import ExplanationService
from app.models import Preferences
from app.orchestration import (
    AmbiguousCourse,
    build_requirement_status_for_student,
    group_ranked_by_category,
    plan_schedules,
    resolve_locked_sections_flexible,
)
from app.parsers.audit import AuditParseError, parse_audit
from app.parsers.catalog import parse_catalog
from app.session_store import InMemorySessionStore, PlanningSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------

MAX_AUDIT_BYTES = 10 * 1024 * 1024    # 10 MiB
MAX_CATALOG_BYTES = 10 * 1024 * 1024  # 10 MiB

_AUDIT_EXTENSIONS = frozenset({".pdf"})
_CATALOG_EXTENSIONS = frozenset({".csv"})

_AUDIT_MIMES = frozenset({
    "application/pdf",
    "application/octet-stream",
})
_CATALOG_MIMES = frozenset({
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
    "text/plain",   # some browsers send this for CSV
})

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Academic Planning Engine",
    version="0.1.0",
    description=(
        "Deterministic academic schedule planner. "
        "Parses a Degree Works audit and course catalog to produce "
        "ranked, valid semester schedules. "
        "No AI model is involved in schedule generation or ranking."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Session store — module-level singleton; tests override via dependency
# ---------------------------------------------------------------------------

_store: InMemorySessionStore = InMemorySessionStore()


def get_session_store() -> InMemorySessionStore:
    return _store


# ---------------------------------------------------------------------------
# Explanation service — module-level singleton; tests override via dependency
# INV-04: only main.py reads environment variables.
# ---------------------------------------------------------------------------


def _create_explanation_service() -> ExplanationService:
    provider_name = os.environ.get("EXPLANATION_PROVIDER", "none")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    model = os.environ.get(
        "ANTHROPIC_EXPLANATION_MODEL", "claude-haiku-4-5-20251001"
    )
    try:
        timeout = float(os.environ.get("EXPLANATION_TIMEOUT_SECONDS", "8"))
    except ValueError:
        timeout = 8.0
    try:
        cache_size = int(os.environ.get("EXPLANATION_CACHE_SIZE", "500"))
    except ValueError:
        cache_size = 500

    provider = load_provider(provider_name, api_key, model, timeout)
    effective_name = (
        provider_name.strip().lower() if provider is not None else "none"
    )
    return ExplanationService(
        provider=provider,
        provider_name=effective_name,
        cache_size=cache_size,
        timeout_seconds=timeout,
    )


_explanation_service: ExplanationService = _create_explanation_service()


def get_explanation_service() -> ExplanationService:
    return _explanation_service


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _error(code: str, message: str, status: int, details: dict[str, Any] | None = None) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details or {})
    )
    return JSONResponse(status_code=status, content=body.model_dump())


def _get_session_or_404(session_id: str, store: InMemorySessionStore) -> PlanningSession:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "Session not found or expired."})
    return session


# ---------------------------------------------------------------------------
# File validation helpers
# ---------------------------------------------------------------------------


def _check_extension(filename: str | None, allowed: frozenset[str], label: str) -> None:
    if not filename:
        return
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in allowed:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_file_type", "message": f"{label} must have extension {sorted(allowed)}"},
        )


def _check_mime(content_type: str | None, allowed: frozenset[str], label: str) -> None:
    if content_type is None:
        return
    ct = content_type.split(";")[0].strip().lower()
    if ct and ct not in allowed:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_content_type", "message": f"{label} content type {ct!r} not accepted"},
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/session", status_code=201, response_model=CreateSessionResponse, tags=["session"])
async def create_session(
    store: Annotated[InMemorySessionStore, Depends(get_session_store)],
) -> CreateSessionResponse:
    """Create a new planning session.  Returns an opaque session ID."""
    session = store.create()
    return CreateSessionResponse(
        session_id=session.session_id,
        created_at=session.created_at.isoformat(),
    )


@app.get("/api/session/{session_id}", response_model=SessionInfoResponse, tags=["session"])
async def get_session_info(
    session_id: str,
    store: Annotated[InMemorySessionStore, Depends(get_session_store)],
) -> SessionInfoResponse:
    session = _get_session_or_404(session_id, store)
    return SessionInfoResponse(
        session_id=session.session_id,
        created_at=session.created_at.isoformat(),
        last_accessed_at=session.last_accessed_at.isoformat(),
        inputs_loaded=session.inputs_loaded,
    )


@app.delete("/api/session/{session_id}", status_code=204, tags=["session"])
async def delete_session(
    session_id: str,
    store: Annotated[InMemorySessionStore, Depends(get_session_store)],
) -> None:
    """Delete a session.  Returns 204 whether or not the session existed."""
    store.delete(session_id)


# ---------------------------------------------------------------------------
# Input upload
# ---------------------------------------------------------------------------


@app.post("/api/session/{session_id}/inputs", response_model=InputSummaryResponse, tags=["inputs"])
async def upload_inputs(
    session_id: str,
    audit_file: Annotated[UploadFile, File(description="Degree Works audit PDF")],
    catalog_file: Annotated[UploadFile, File(description="Course catalog CSV")],
    preferences_json: Annotated[str | None, Form(description="JSON-encoded preferences (optional)")] = None,
    store: InMemorySessionStore = Depends(get_session_store),
) -> InputSummaryResponse:
    """Parse and store the uploaded audit PDF and catalog CSV.

    Raw file bytes are never persisted beyond this handler.
    Student name and ID are excluded from the response.
    """
    session = _get_session_or_404(session_id, store)

    # --- Validate and read audit ---
    _check_extension(audit_file.filename, _AUDIT_EXTENSIONS, "Audit file")
    _check_mime(audit_file.content_type, _AUDIT_MIMES, "Audit file")
    audit_bytes = await audit_file.read()
    if len(audit_bytes) > MAX_AUDIT_BYTES:
        raise HTTPException(status_code=413, detail={"code": "audit_too_large", "message": f"Audit file exceeds {MAX_AUDIT_BYTES // 1024 // 1024} MiB limit."})

    # --- Validate and read catalog ---
    _check_extension(catalog_file.filename, _CATALOG_EXTENSIONS, "Catalog file")
    _check_mime(catalog_file.content_type, _CATALOG_MIMES, "Catalog file")
    catalog_bytes = await catalog_file.read()
    if len(catalog_bytes) > MAX_CATALOG_BYTES:
        raise HTTPException(status_code=413, detail={"code": "catalog_too_large", "message": f"Catalog file exceeds {MAX_CATALOG_BYTES // 1024 // 1024} MiB limit."})

    # --- Parse (bytes are local; not stored in session) ---
    try:
        student = parse_audit(BytesIO(audit_bytes))
    except AuditParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "audit_parse_failed", "message": "File does not appear to be a valid Swarthmore Degree Works audit."},
        ) from exc
    except Exception as exc:
        logger.error("Unexpected audit parse error", exc_info=True)
        raise HTTPException(
            status_code=422,
            detail={"code": "audit_parse_failed", "message": "File does not appear to be a valid Swarthmore Degree Works audit."},
        ) from exc
    # Discard bytes immediately.
    del audit_bytes

    try:
        catalog_text = catalog_bytes.decode("utf-8", errors="replace")
        del catalog_bytes
        catalog_sections = parse_catalog(StringIO(catalog_text))
        del catalog_text
    except Exception as exc:
        logger.error("Unexpected catalog parse error", exc_info=True)
        raise HTTPException(
            status_code=422,
            detail={"code": "catalog_parse_failed", "message": "File does not appear to be a valid course catalog CSV."},
        ) from exc

    # --- Build requirement status ---
    try:
        req_build = build_requirement_status_for_student(student)
    except UnsupportedProgramError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_program",
                "message": (
                    "No requirement definitions are available for this student's "
                    "major and catalog year. Only Swarthmore CS major (catalog year 202304) "
                    "is supported in this version."
                ),
            },
        ) from exc

    # --- Parse preferences (optional) ---
    parsed_prefs: Preferences | None = None
    if preferences_json:
        import json
        from pydantic import ValidationError
        from app.api_models import PreferencesRequest
        try:
            prefs_data = json.loads(preferences_json)
            prefs_request = PreferencesRequest.model_validate(prefs_data)
            parsed_prefs = preferences_from_request(prefs_request)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_preferences", "message": f"Invalid preferences: {exc}"},
            ) from exc

    # --- Store parsed objects; discard raw data ---
    session.student = student
    session.catalog_sections = catalog_sections
    session.requirement_status = req_build.requirement_status
    session.parser_warnings = req_build.warnings
    if parsed_prefs is not None:
        session.preferences = parsed_prefs
    # Clear stale planning results so old schedule IDs are not accessible.
    session.latest_ranked_schedules = ()
    session.latest_search_cap_reached = False
    session.latest_preferences = None
    store.update(session)

    return InputSummaryResponse(
        session_id=session_id,
        student_summary=student_summary(student),
        catalog_summary=CatalogSummaryResponse(parent_sections=len(catalog_sections)),
        requirement_summary=requirement_summary(
            req_build.requirement_status, req_build.unmatched_ids
        ),
        warnings=req_build.warnings,
    )


# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------


@app.post("/api/session/{session_id}/schedules", tags=["schedules"])
async def generate_schedules_endpoint(
    session_id: str,
    request: GenerateSchedulesRequest,
    store: Annotated[InMemorySessionStore, Depends(get_session_store)],
) -> JSONResponse:
    """Run the full planning pipeline and return ranked schedules or a diagnostic.

    INV-API-06: No domain logic here — all computation is in orchestration.py.
    INV-API-09: Locked-section ambiguity is never resolved arbitrarily.
    INV-API-10: Solver cap is always disclosed.
    INV-API-12: Empty result returns diagnostic, not HTTP 500.
    """
    session = _get_session_or_404(session_id, store)

    if not session.inputs_loaded:
        return _error(
            "inputs_not_uploaded",
            "Upload audit and catalog files before generating schedules.",
            status=409,
        )

    student = session.student
    catalog_sections = session.catalog_sections
    req_status = session.requirement_status
    assert student is not None
    assert catalog_sections is not None
    assert req_status is not None

    # Use preferences from request; fall back to session default if present.
    prefs = preferences_from_request(request.preferences)
    locked_ref_nos: frozenset[str] = frozenset(request.locked_ref_nos)

    # --- Validate explicit locked ref_nos ---
    by_ref = {s.ref_no: s for s in catalog_sections if s.is_parent}
    unknown_refs = [r for r in locked_ref_nos if r not in by_ref]
    if unknown_refs:
        return _error(
            "unknown_locked_ref",
            f"Unknown catalog ref_no(s): {unknown_refs}. "
            "Provide valid ref_nos from the uploaded catalog.",
            status=422,
            details={"unknown_refs": unknown_refs},
        )

    # --- Check for ambiguous preregistered courses ---
    if prefs.lock_preregistered and student.preregistered_courses:
        resolution = resolve_locked_sections_flexible(
            student.preregistered_courses, catalog_sections, locked_ref_nos
        )
        if resolution.ambiguous_courses:
            choices_payload = _ambiguity_payload(resolution.ambiguous_courses)
            return _error(
                "locked_section_selection_required",
                "One or more preregistered courses have multiple catalog sections. "
                "Specify the exact section ref_no(s) in locked_ref_nos.",
                status=409,
                details={"courses": [c.model_dump() for c in choices_payload]},
            )

    # --- Run planning pipeline ---
    try:
        result = plan_schedules(
            student=student,
            catalog_sections=catalog_sections,
            requirement_status=req_status,
            preferences=prefs,
            locked_ref_nos=locked_ref_nos,
            max_results=request.max_results,
            max_ranked=request.max_ranked,
        )
    except InvalidLockedScheduleError as exc:
        return _error(
            "invalid_locked_schedule",
            "The locked sections violate a hard constraint. "
            "Check for time conflicts or credit-limit violations.",
            status=422,
            details={"detail": str(exc)},
        )
    except LinkedSectionError as exc:
        return _error(
            "linked_section_error",
            "A course has an unsupported linked-section configuration.",
            status=422,
            details={"detail": str(exc)},
        )
    except Exception:
        logger.error("Unexpected planning error", exc_info=True)
        return _error("planning_error", "An unexpected error occurred during schedule generation.", status=500)

    meta = SearchMetadataResponse(
        candidate_count=result.candidate_count,
        option_count=result.option_count,
        generated_schedules=result.generated_count,
        solver_cap=result.max_results,
        cap_reached=result.solver_cap_reached,
        search_space_fully_enumerated=not result.solver_cap_reached,
    )

    # Persist planning results for the explanation endpoint, regardless of
    # whether schedules were found.  Stale results from prior generations
    # are replaced here.  INV-AI-08: no provider call occurs in this handler.
    session.latest_ranked_schedules = result.ranked_schedules
    session.latest_search_cap_reached = result.solver_cap_reached
    session.latest_preferences = prefs
    store.update(session)

    if result.diagnostic is not None:
        response = DiagnosticResultResponse(
            status="no_valid_schedules",
            diagnostic=diagnostic_to_response(result.diagnostic),
            search_metadata=meta,
        )
        return JSONResponse(status_code=200, content=response.model_dump())

    # --- Build grouped schedule response ---
    top_sched, categories = group_ranked_by_category(result.ranked_schedules)

    top_resp = [ranked_schedule_to_response(rs) for rs in top_sched]
    cat_resp = {
        cat: [ranked_schedule_to_response(rs) for rs in sched_list]
        for cat, sched_list in categories.items()
    }

    response_obj = ScheduleResultResponse(
        status="schedules_found",
        top_schedules=top_resp,
        categories=cat_resp,
        search_metadata=meta,
    )
    return JSONResponse(status_code=200, content=response_obj.model_dump())


# ---------------------------------------------------------------------------
# Ambiguity helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Explanation endpoint
# ---------------------------------------------------------------------------


@app.post(
    "/api/session/{session_id}/schedules/{schedule_id}/explanation",
    response_model=ExplanationResponse,
    tags=["explanations"],
)
async def explain_schedule_endpoint(
    session_id: str,
    schedule_id: str,
    store: Annotated[InMemorySessionStore, Depends(get_session_store)],
    service: Annotated[ExplanationService, Depends(get_explanation_service)],
) -> JSONResponse:
    """Return a natural-language explanation for one ranked schedule.

    Explanations are generated lazily (separately from schedule generation).
    The explanation comes from a deterministic fallback or an optional
    AI provider, whichever is configured.

    Provider unavailability or invalid output returns HTTP 200 with
    source="fallback" — it is never a client-visible error.

    INV-AI-08: POST /schedules never calls the explanation provider.
    INV-AI-09: Provider calls occur only through ExplanationService.
    """
    session = _get_session_or_404(session_id, store)

    if not session.inputs_loaded:
        return _error(
            "inputs_not_uploaded",
            "Upload audit and catalog files before requesting explanations.",
            status=409,
        )

    if not session.latest_ranked_schedules:
        return _error(
            "no_planning_results",
            "Generate schedules before requesting explanations.",
            status=409,
        )

    # Build lookup from deterministic schedule ID → RankedSchedule.
    ranked_by_id = {
        compute_schedule_id(rs.schedule): rs
        for rs in session.latest_ranked_schedules
    }
    if schedule_id not in ranked_by_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "schedule_not_found",
                "message": (
                    "Schedule ID not found in the most recent planning results. "
                    "Generate schedules again to obtain valid IDs."
                ),
            },
        )

    ranked_schedule = ranked_by_id[schedule_id]
    prefs = session.latest_preferences or Preferences()

    try:
        result = await service.explain(
            ranked_schedule,
            prefs,
            schedule_id=schedule_id,
            solver_cap_reached=session.latest_search_cap_reached,
        )
    except Exception:
        logger.error("explanation_service_error")
        return _error(
            "explanation_error",
            "An unexpected error occurred during explanation.",
            status=500,
        )

    response_body = ExplanationResponse(
        schedule_id=schedule_id,
        explanation=result.text,
        source=result.source,
    )
    return JSONResponse(status_code=200, content=response_body.model_dump())


# ---------------------------------------------------------------------------
# Ambiguity helper
# ---------------------------------------------------------------------------


def _ambiguity_payload(
    ambiguous: tuple[AmbiguousCourse, ...],
) -> list[AmbiguousCourseError]:
    result = []
    for ac in ambiguous:
        choices = [
            AmbiguousChoiceSection(
                ref_no=s.ref_no,
                section_id=s.section_id,
                meeting_times=[
                    MeetingTimeResponse(
                        days=list(mt.days),
                        start=f"{mt.start.hour:02d}:{mt.start.minute:02d}",
                        end=f"{mt.end.hour:02d}:{mt.end.minute:02d}",
                    )
                    for mt in s.meeting_times
                ],
            )
            for s in ac.choices
        ]
        result.append(AmbiguousCourseError(course_code=ac.course_code, choices=choices))
    return result
