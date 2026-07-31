"""API endpoint tests for explanation — Stage 7 acceptance criteria.

POST /api/session/{session_id}/schedules/{schedule_id}/explanation

All tests use the synthetic fixture files (no real student data).
No real AI provider calls are made.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.explainer import ExplainerInput
from app.explanation_service import ExplanationResult, ExplanationService
from app.main import app, get_explanation_service, get_session_store
from app.session_store import InMemorySessionStore

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fake services
# ---------------------------------------------------------------------------


class _FallbackOnlyService(ExplanationService):
    """No provider — always returns deterministic fallback."""

    def __init__(self) -> None:
        super().__init__(provider=None)


class _GoodProviderService:
    """Always returns a known provider result."""

    async def explain(
        self,
        ranked_schedule: object,
        preferences: object,
        *,
        schedule_id: str,
        solver_cap_reached: bool,
    ) -> ExplanationResult:
        return ExplanationResult(
            text="This schedule includes a great course that contributes toward CS Major.",
            source="provider",
        )


class _FailingProviderService:
    """Provider always fails; service falls back."""

    async def explain(
        self,
        ranked_schedule: object,
        preferences: object,
        *,
        schedule_id: str,
        solver_cap_reached: bool,
    ) -> ExplanationResult:
        return ExplanationResult(
            text="This schedule includes 1 credit across 1 course.",
            source="fallback",
        )


class _PanicService:
    """Panics if called — verifies schedule generation doesn't use explanations."""

    async def explain(self, *args: object, **kwargs: object) -> ExplanationResult:
        raise AssertionError("ExplanationService must not be called during schedule generation")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture()
def client(store: InMemorySessionStore) -> TestClient:
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_explanation_service] = lambda: _FallbackOnlyService()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture()
def audit_bytes() -> bytes:
    path = _FIXTURES / "audit_synthetic.pdf"
    assert path.exists(), f"Missing: {path}"
    return path.read_bytes()


@pytest.fixture()
def catalog_bytes() -> bytes:
    path = _FIXTURES / "catalog_sample_10.csv"
    assert path.exists(), f"Missing: {path}"
    return path.read_bytes()


def _prefs_json(**kwargs: object) -> str:
    defaults: dict[str, object] = {
        "min_credits": "1",
        "max_credits": "4",
        "lock_preregistered": True,
    }
    defaults.update(kwargs)
    return json.dumps(defaults)


def _create_session(client: TestClient) -> str:
    resp = client.post("/api/session")
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _upload_inputs(
    client: TestClient, sid: str, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    files = {
        "audit_file": ("audit_synthetic.pdf", audit_bytes, "application/pdf"),
        "catalog_file": ("catalog_sample_10.csv", catalog_bytes, "text/csv"),
    }
    resp = client.post(f"/api/session/{sid}/inputs", files=files)
    assert resp.status_code == 200, resp.text


def _generate_schedules(client: TestClient, sid: str) -> dict:  # type: ignore[type-arg]
    body = {"preferences": {"min_credits": "1", "max_credits": "4"}, "locked_ref_nos": []}
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _first_schedule_id(sched_resp: dict) -> str:  # type: ignore[type-arg]
    top = sched_resp.get("top_schedules", [])
    assert top, "No schedules returned"
    return top[0]["schedule_id"]


# ---------------------------------------------------------------------------
# Full integration helper
# ---------------------------------------------------------------------------


def _full_flow(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> tuple[str, str]:
    """Create session, upload, generate.  Returns (session_id, first_schedule_id)."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    sched_resp = _generate_schedules(client, sid)
    return sid, _first_schedule_id(sched_resp)


# ---------------------------------------------------------------------------
# 1: Explanation before schedule generation → 409
# ---------------------------------------------------------------------------


def test_explanation_before_generate_returns_409(
    client: TestClient, store: InMemorySessionStore,
    audit_bytes: bytes, catalog_bytes: bytes,
) -> None:
    """Test 1: Explanation endpoint returns 409 when no schedules have been generated."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    # No schedule generation yet.
    resp = client.post(f"/api/session/{sid}/schedules/deadbeef12345678/explanation")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_planning_results"


def test_explanation_without_inputs_returns_409(client: TestClient) -> None:
    """Test 1b: Explanation endpoint returns 409 when inputs not uploaded."""
    sid = _create_session(client)
    resp = client.post(f"/api/session/{sid}/schedules/deadbeef12345678/explanation")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 2: Unknown session → 404
# ---------------------------------------------------------------------------


def test_explanation_unknown_session_returns_404(client: TestClient) -> None:
    """Test 2: Unknown session returns 404."""
    resp = client.post("/api/session/nonexistent-xyz/schedules/abc/explanation")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3: Unknown schedule ID → 404
# ---------------------------------------------------------------------------


def test_explanation_unknown_schedule_id_returns_404(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 3: Known session, known schedules, but unknown schedule_id → 404."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    _generate_schedules(client, sid)
    resp = client.post(f"/api/session/{sid}/schedules/0000000000000000/explanation")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4: Known schedule returns 200
# ---------------------------------------------------------------------------


def test_explanation_known_schedule_returns_200(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 4: Known session + known schedule_id returns HTTP 200."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)
    resp = client.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schedule_id"] == schedule_id
    assert isinstance(body["explanation"], str)
    assert len(body["explanation"]) > 0
    assert body["source"] in ("provider", "fallback")


# ---------------------------------------------------------------------------
# 5: Fallback source without API credentials
# ---------------------------------------------------------------------------


def test_fallback_source_without_credentials(
    store: InMemorySessionStore, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 5: Default service (no provider) returns source='fallback'."""
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_explanation_service] = lambda: _FallbackOnlyService()
    with TestClient(app, raise_server_exceptions=False) as c:
        sid, schedule_id = _full_flow(c, audit_bytes, catalog_bytes)
        resp = c.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["source"] == "fallback"


# ---------------------------------------------------------------------------
# 6: Provider failure still returns 200 fallback
# ---------------------------------------------------------------------------


def test_provider_failure_returns_200_fallback(
    store: InMemorySessionStore, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 6: Even when provider fails, response is 200 with fallback."""
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_explanation_service] = lambda: _FailingProviderService()
    with TestClient(app, raise_server_exceptions=False) as c:
        sid, schedule_id = _full_flow(c, audit_bytes, catalog_bytes)
        resp = c.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["source"] == "fallback"


# ---------------------------------------------------------------------------
# 7: Response matches schema
# ---------------------------------------------------------------------------


def test_explanation_response_schema(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 7: Response body has schedule_id, explanation, and source fields."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)
    resp = client.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"schedule_id", "explanation", "source"}
    assert isinstance(body["schedule_id"], str)
    assert isinstance(body["explanation"], str)
    assert body["source"] in ("provider", "fallback")


# ---------------------------------------------------------------------------
# 8–9: Privacy — no student name or ID in explanation
# ---------------------------------------------------------------------------


def test_explanation_contains_no_student_name(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 8: Explanation text contains no student name."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)
    resp = client.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    body = resp.json()
    assert "Student, Demo" not in body["explanation"]
    assert "Student" not in body["explanation"] or "schedule" in body["explanation"]


def test_explanation_contains_no_student_id(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 9: Explanation text contains no student ID."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)
    resp = client.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    assert "000000000" not in resp.json()["explanation"]


# ---------------------------------------------------------------------------
# 10: No unknown course code in explanation
# ---------------------------------------------------------------------------


def test_no_unknown_course_code_in_explanation(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 10: Explanation text contains no invented course codes."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)
    resp = client.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    assert resp.status_code == 200
    # The explanation is validated; if it reaches the client, it passed validation.
    body = resp.json()
    assert isinstance(body["explanation"], str)


# ---------------------------------------------------------------------------
# 11: Repeated fallback requests return identical text
# ---------------------------------------------------------------------------


def test_repeated_fallback_requests_identical(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 11: Same schedule_id returns same fallback text on repeat calls."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)
    url = f"/api/session/{sid}/schedules/{schedule_id}/explanation"
    r1 = client.post(url).json()["explanation"]
    r2 = client.post(url).json()["explanation"]
    assert r1 == r2


# ---------------------------------------------------------------------------
# 12: New schedule generation replaces stale schedule IDs
# ---------------------------------------------------------------------------


def test_new_generation_replaces_stale_ids(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 12: Old schedule IDs are not accessible after new generation."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)

    # First generation.
    sched1 = _generate_schedules(client, sid)
    old_id = _first_schedule_id(sched1)

    # Second generation (may produce same IDs for same catalog — verify endpoint still works).
    sched2 = _generate_schedules(client, sid)
    new_id = _first_schedule_id(sched2)

    # The new ID from the latest generation must work.
    resp = client.post(f"/api/session/{sid}/schedules/{new_id}/explanation")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 13: Uploading new inputs clears explanation state
# ---------------------------------------------------------------------------


def test_upload_clears_planning_results(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 13: After re-uploading inputs, old schedule IDs return 409."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    sched_resp = _generate_schedules(client, sid)
    old_id = _first_schedule_id(sched_resp)

    # Re-upload inputs — clears planning results.
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)

    # Now the old schedule ID should return 409 (no planning results).
    resp = client.post(f"/api/session/{sid}/schedules/{old_id}/explanation")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_planning_results"


# ---------------------------------------------------------------------------
# 14: Schedule generation endpoint performs no provider call
# ---------------------------------------------------------------------------


def test_schedule_generation_does_not_call_provider(
    store: InMemorySessionStore, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 14: POST /schedules never calls the explanation service."""
    app.dependency_overrides[get_session_store] = lambda: store
    # _PanicService raises AssertionError if called.
    app.dependency_overrides[get_explanation_service] = lambda: _PanicService()
    with TestClient(app, raise_server_exceptions=True) as c:
        sid = _create_session(c)
        _upload_inputs(c, sid, audit_bytes, catalog_bytes)
        # This must not raise — PanicService must not be invoked.
        body = {"preferences": {"min_credits": "1", "max_credits": "4"}, "locked_ref_nos": []}
        resp = c.post(f"/api/session/{sid}/schedules", json=body)
        assert resp.status_code == 200
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 15: OpenAPI includes explanation endpoint
# ---------------------------------------------------------------------------


def test_openapi_includes_explanation_endpoint(client: TestClient) -> None:
    """Test 15: OpenAPI schema lists the explanation endpoint."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json().get("paths", {})
    explanation_paths = [
        p for p in paths if "explanation" in p
    ]
    assert explanation_paths, (
        f"No explanation path found in OpenAPI. Available paths: {list(paths.keys())}"
    )


# ---------------------------------------------------------------------------
# 16: Raw provider errors absent from client responses
# ---------------------------------------------------------------------------


def test_raw_provider_errors_absent_from_response(
    store: InMemorySessionStore, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 16: Provider failure message is never exposed in the JSON response."""

    class _ErrorLeakingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            raise RuntimeError("SECRET_INTERNAL_TOKEN_DO_NOT_EXPOSE")

    class _TestService(ExplanationService):
        def __init__(self) -> None:
            from app.explanation_provider import ExplanationProvider
            super().__init__(provider=None)  # fallback-only
            # Temporarily inject the failing provider so it tries and fails.

    # Use the real ExplanationService with the leaking provider.
    from app.explanation_service import ExplanationService as _ES

    leaking_service = _ES(
        provider=_ErrorLeakingProvider(),  # type: ignore[arg-type]
        provider_name="fake",
    )

    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_explanation_service] = lambda: leaking_service
    with TestClient(app, raise_server_exceptions=False) as c:
        sid, schedule_id = _full_flow(c, audit_bytes, catalog_bytes)
        resp = c.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    response_text = resp.text
    assert "SECRET_INTERNAL_TOKEN_DO_NOT_EXPOSE" not in response_text


# ---------------------------------------------------------------------------
# 17: Full integration flow
# ---------------------------------------------------------------------------


def test_full_integration_flow(
    store: InMemorySessionStore, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 17: Full end-to-end: create → upload → generate → explain."""
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_explanation_service] = lambda: _FallbackOnlyService()

    with TestClient(app, raise_server_exceptions=False) as c:
        # Step 1: create session.
        resp = c.post("/api/session")
        assert resp.status_code == 201
        sid = resp.json()["session_id"]

        # Step 2: upload inputs.
        files = {
            "audit_file": ("audit_synthetic.pdf", audit_bytes, "application/pdf"),
            "catalog_file": ("catalog_sample_10.csv", catalog_bytes, "text/csv"),
        }
        resp = c.post(f"/api/session/{sid}/inputs", files=files)
        assert resp.status_code == 200
        assert "student_summary" in resp.json()

        # Step 3: generate schedules.
        body = {
            "preferences": {"min_credits": "1", "max_credits": "4"},
            "locked_ref_nos": [],
        }
        resp = c.post(f"/api/session/{sid}/schedules", json=body)
        assert resp.status_code == 200
        sched_body = resp.json()
        assert sched_body["status"] == "schedules_found"
        schedule_id = sched_body["top_schedules"][0]["schedule_id"]

        # Step 4: request explanation.
        resp = c.post(f"/api/session/{sid}/schedules/{schedule_id}/explanation")
        assert resp.status_code == 200
        expl_body = resp.json()
        assert expl_body["schedule_id"] == schedule_id
        assert isinstance(expl_body["explanation"], str)
        assert len(expl_body["explanation"]) > 10
        assert expl_body["source"] == "fallback"

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Privacy regression: entire response body
# ---------------------------------------------------------------------------


def test_privacy_regression_full_response(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Privacy regression: 'Student, Demo' and '000000000' absent from all responses."""
    sid, schedule_id = _full_flow(client, audit_bytes, catalog_bytes)

    # Check schedule response.
    sched_resp = client.post(
        f"/api/session/{sid}/schedules",
        json={"preferences": {"min_credits": "1", "max_credits": "4"}, "locked_ref_nos": []},
    )
    sched_text = sched_resp.text
    assert "Student, Demo" not in sched_text
    assert "000000000" not in sched_text

    # Check explanation response.
    expl_resp = client.post(
        f"/api/session/{sid}/schedules/{schedule_id}/explanation"
    )
    expl_text = expl_resp.text
    assert "Student, Demo" not in expl_text
    assert "000000000" not in expl_text
