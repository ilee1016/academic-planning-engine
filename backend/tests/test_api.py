"""API endpoint tests using FastAPI TestClient.

All tests use the synthetic fixture files (no real student data).
PII regression tests assert the JSON body contains no student name or ID.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_session_store
from app.session_store import InMemorySessionStore

_FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture()
def client(store: InMemorySessionStore) -> TestClient:
    """Return a TestClient that uses an isolated session store."""
    app.dependency_overrides[get_session_store] = lambda: store
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
    client: TestClient,
    session_id: str,
    audit_bytes: bytes,
    catalog_bytes: bytes,
    prefs: str | None = None,
) -> dict:  # type: ignore[type-arg]
    files = {
        "audit_file": ("audit_synthetic.pdf", audit_bytes, "application/pdf"),
        "catalog_file": ("catalog_sample_10.csv", catalog_bytes, "text/csv"),
    }
    data: dict[str, str] = {}
    if prefs:
        data["preferences_json"] = prefs
    resp = client.post(f"/api/session/{session_id}/inputs", files=files, data=data)
    return resp


# ---------------------------------------------------------------------------
# 1. POST /api/session returns 201 and session ID
# ---------------------------------------------------------------------------


def test_create_session_returns_201(client: TestClient) -> None:
    """Test 1: Session creation returns HTTP 201 with session_id and created_at."""
    resp = client.post("/api/session")
    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body
    assert "created_at" in body
    assert len(body["session_id"]) > 10


# ---------------------------------------------------------------------------
# 2. Unknown session returns 404
# ---------------------------------------------------------------------------


def test_unknown_session_returns_404(client: TestClient) -> None:
    """Test 2: Accessing an unknown session returns 404."""
    resp = client.get("/api/session/nonexistent-xyz")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Generate before upload returns 409
# ---------------------------------------------------------------------------


def test_generate_before_upload_returns_409(client: TestClient) -> None:
    """Test 3: Schedule generation without prior upload returns 409."""
    sid = _create_session(client)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    assert resp.status_code == 409
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "inputs_not_uploaded"


# ---------------------------------------------------------------------------
# 4. Valid upload succeeds
# ---------------------------------------------------------------------------


def test_valid_upload_succeeds(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 4: Valid PDF + CSV upload returns 200 with input summary."""
    sid = _create_session(client)
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == sid
    assert "student_summary" in body
    assert "catalog_summary" in body
    assert "requirement_summary" in body


# ---------------------------------------------------------------------------
# 5. Student name and ID absent from upload response
# ---------------------------------------------------------------------------


def test_student_identity_absent_from_upload_response(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 5: Upload response must not contain student name or ID."""
    sid = _create_session(client)
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    assert resp.status_code == 200
    raw = resp.text
    # The synthetic fixture uses "Student, Demo" / "000000000"
    assert "Student" not in raw
    assert "Demo" not in raw
    assert "000000000" not in raw


# ---------------------------------------------------------------------------
# 6. Invalid PDF returns 422
# ---------------------------------------------------------------------------


def test_invalid_pdf_returns_422(
    client: TestClient, catalog_bytes: bytes
) -> None:
    """Test 6: A non-PDF file as audit returns 422."""
    sid = _create_session(client)
    bad_pdf = b"this is not a pdf file at all"
    resp = _upload_inputs(client, sid, bad_pdf, catalog_bytes)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. Invalid CSV returns 422 (wrong extension)
# ---------------------------------------------------------------------------


def test_invalid_csv_extension_returns_422(
    client: TestClient, audit_bytes: bytes
) -> None:
    """Test 7: A file with .txt extension for catalog returns 422."""
    sid = _create_session(client)
    files = {
        "audit_file": ("audit.pdf", audit_bytes, "application/pdf"),
        "catalog_file": ("catalog.txt", b"not,a,csv", "text/plain"),
    }
    resp = client.post(f"/api/session/{sid}/inputs", files=files)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 8. Oversized audit returns 413
# ---------------------------------------------------------------------------


def test_oversized_audit_returns_413(
    client: TestClient, catalog_bytes: bytes
) -> None:
    """Test 8: Audit file exceeding 10 MiB returns 413."""
    sid = _create_session(client)
    big_audit = b"X" * (10 * 1024 * 1024 + 1)
    # Wrap in fake PDF header so extension check passes
    files = {
        "audit_file": ("audit.pdf", b"%PDF-" + big_audit, "application/pdf"),
        "catalog_file": ("catalog.csv", catalog_bytes, "text/csv"),
    }
    resp = client.post(f"/api/session/{sid}/inputs", files=files)
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# 9. Oversized catalog returns 413
# ---------------------------------------------------------------------------


def test_oversized_catalog_returns_413(
    client: TestClient, audit_bytes: bytes
) -> None:
    """Test 9: Catalog file exceeding 10 MiB returns 413."""
    sid = _create_session(client)
    big_catalog = b"a,b,c\n" + b"x," * (10 * 1024 * 1024)
    files = {
        "audit_file": ("audit.pdf", audit_bytes, "application/pdf"),
        "catalog_file": ("catalog.csv", big_catalog, "text/csv"),
    }
    resp = client.post(f"/api/session/{sid}/inputs", files=files)
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# 10. Invalid preferences return 422
# ---------------------------------------------------------------------------


def test_invalid_preferences_in_upload_return_422(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 10: Malformed preferences JSON in upload returns 422."""
    sid = _create_session(client)
    bad_prefs = '{"min_credits": "5", "max_credits": "2"}'  # min > max
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes, prefs=bad_prefs)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 11. Valid schedule generation returns 200
# ---------------------------------------------------------------------------


def test_valid_schedule_generation_returns_200(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 11: Schedule generation returns 200 with result."""
    sid = _create_session(client)
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    assert resp.status_code == 200

    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp2 = client.post(f"/api/session/{sid}/schedules", json=body)
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] in ("schedules_found", "no_valid_schedules")


# ---------------------------------------------------------------------------
# 12. No-schedule outcome returns 200 with diagnostic
# ---------------------------------------------------------------------------


def test_no_schedule_returns_200_with_diagnostic(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 12: When no schedules exist, response is 200 with diagnostic, not 500."""
    sid = _create_session(client)
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    assert resp.status_code == 200

    # Impossible: require all days free — no section can be scheduled
    body = {
        "preferences": {"min_credits": "1", "max_credits": "8",
                        "free_days": ["M", "T", "W", "R", "F"]},
        "locked_ref_nos": [],
    }
    resp2 = client.post(f"/api/session/{sid}/schedules", json=body)
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "no_valid_schedules"
    assert "diagnostic" in data
    assert data["diagnostic"]["no_valid_schedules"] is True


# ---------------------------------------------------------------------------
# 13. Schedule response matches schema
# ---------------------------------------------------------------------------


def test_schedule_response_matches_schema(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 13: Schedule response contains expected top-level fields."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert "search_metadata" in data
    meta = data["search_metadata"]
    assert "generated_schedules" in meta
    assert "solver_cap" in meta
    assert "cap_reached" in meta
    assert "search_space_fully_enumerated" in meta


# ---------------------------------------------------------------------------
# 14. Decimal credits serialize consistently
# ---------------------------------------------------------------------------


def test_decimal_credits_in_response_are_strings(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 14: Credits in schedule response are serialized as strings."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    data = resp.json()
    if data.get("status") == "schedules_found" and data.get("top_schedules"):
        sched = data["top_schedules"][0]
        assert isinstance(sched["total_credits"], str)


# ---------------------------------------------------------------------------
# 15. Schedule IDs stable across repeated requests
# ---------------------------------------------------------------------------


def test_schedule_ids_stable_across_requests(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 15: Same inputs produce the same schedule IDs on repeated requests."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp1 = client.post(f"/api/session/{sid}/schedules", json=body)
    resp2 = client.post(f"/api/session/{sid}/schedules", json=body)
    d1 = resp1.json()
    d2 = resp2.json()
    if d1.get("status") == "schedules_found" and d1.get("top_schedules"):
        ids1 = [s["schedule_id"] for s in d1["top_schedules"]]
        ids2 = [s["schedule_id"] for s in d2["top_schedules"]]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# 16. Cap disclosure appears in response
# ---------------------------------------------------------------------------


def test_cap_disclosure_in_response(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 16: search_metadata.cap_reached is always present."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    data = resp.json()
    assert "search_metadata" in data
    assert "cap_reached" in data["search_metadata"]
    assert "search_space_fully_enumerated" in data["search_metadata"]


# ---------------------------------------------------------------------------
# 17. No raw parser exception in client response
# ---------------------------------------------------------------------------


def test_no_raw_exception_in_error_response(
    client: TestClient, catalog_bytes: bytes
) -> None:
    """Test 17: Malformed upload returns a sanitized error, not raw traceback."""
    sid = _create_session(client)
    not_a_pdf = b"this is just text"
    resp = _upload_inputs(client, sid, not_a_pdf, catalog_bytes)
    assert resp.status_code == 422
    raw = resp.text
    assert "Traceback" not in raw
    assert "pdfplumber" not in raw
    assert "Exception" not in raw or "error" in raw.lower()


# ---------------------------------------------------------------------------
# 18. DELETE session works
# ---------------------------------------------------------------------------


def test_delete_session(client: TestClient) -> None:
    """Test 18: DELETE /api/session/{id} returns 204."""
    sid = _create_session(client)
    resp = client.delete(f"/api/session/{sid}")
    assert resp.status_code == 204
    # Now get should return 404
    resp2 = client.get(f"/api/session/{sid}")
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# 19. Expired session returns 404
# ---------------------------------------------------------------------------


def test_expired_session_returns_404(client: TestClient, store: InMemorySessionStore) -> None:
    """Test 19: An expired session is treated as not found."""
    from datetime import timedelta
    from app.session_store import InMemorySessionStore as IMS
    tiny_store = IMS(ttl=timedelta(seconds=0))
    app.dependency_overrides[get_session_store] = lambda: tiny_store
    try:
        resp = TestClient(app, raise_server_exceptions=False).post("/api/session")
        assert resp.status_code == 201
        sid = resp.json()["session_id"]
        resp2 = TestClient(app, raise_server_exceptions=False).get(f"/api/session/{sid}")
        assert resp2.status_code == 404
    finally:
        app.dependency_overrides[get_session_store] = lambda: store


# ---------------------------------------------------------------------------
# 20. Health endpoint
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_200(client: TestClient) -> None:
    """Test 20: GET /health returns 200 with status: ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 21. OpenAPI schema generates successfully
# ---------------------------------------------------------------------------


def test_openapi_schema_generated(client: TestClient) -> None:
    """Test 21: /openapi.json is valid and accessible."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "openapi" in schema
    assert "paths" in schema


# ---------------------------------------------------------------------------
# 22. Unknown locked ref returns 422
# ---------------------------------------------------------------------------


def test_unknown_locked_ref_returns_422(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 22: Unknown ref_no in locked_ref_nos returns 422."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": ["99999999"],  # not in catalog
    }
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == "unknown_locked_ref"


# ---------------------------------------------------------------------------
# 23. Privacy regression: no PII in any response
# ---------------------------------------------------------------------------


def test_no_pii_in_upload_response(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 23: Upload response JSON body contains no student identity fields."""
    sid = _create_session(client)
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    assert resp.status_code == 200
    body = resp.text
    # Synthetic fixture uses "Student, Demo" and "000000000"
    for pii in ["Student, Demo", "000000000"]:
        assert pii not in body, f"PII found in response: {pii!r}"


def test_no_pii_in_schedule_response(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 23b: Schedule response contains no student name or ID."""
    sid = _create_session(client)
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    body = {
        "preferences": {"min_credits": "1", "max_credits": "4"},
        "locked_ref_nos": [],
    }
    resp = client.post(f"/api/session/{sid}/schedules", json=body)
    raw = resp.text
    for pii in ["Student, Demo", "000000000"]:
        assert pii not in raw, f"PII found in schedule response: {pii!r}"


def test_no_filesystem_paths_in_response(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 23c: Responses do not contain local filesystem paths."""
    sid = _create_session(client)
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    raw = resp.text
    # Should not contain path separators from stack traces
    assert "/Users/" not in raw
    assert "C:\\" not in raw


# ---------------------------------------------------------------------------
# 24. Session GET info works
# ---------------------------------------------------------------------------


def test_get_session_info(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 24: GET /api/session/{id} returns session metadata."""
    sid = _create_session(client)
    resp = client.get(f"/api/session/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == sid
    assert data["inputs_loaded"] is False
    # After upload
    _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    resp2 = client.get(f"/api/session/{sid}")
    assert resp2.status_code == 200
    assert resp2.json()["inputs_loaded"] is True


# ---------------------------------------------------------------------------
# 25. Integration: create → upload → schedule → verify determinism → delete
# ---------------------------------------------------------------------------


def test_api_integration_full_flow(
    client: TestClient, audit_bytes: bytes, catalog_bytes: bytes
) -> None:
    """Test 25: End-to-end API flow with deterministic IDs and clean teardown."""
    # 1. Create session
    resp = client.post("/api/session")
    assert resp.status_code == 201
    sid = resp.json()["session_id"]

    # 2. Upload inputs
    resp = _upload_inputs(client, sid, audit_bytes, catalog_bytes)
    assert resp.status_code == 200
    assert resp.json()["catalog_summary"]["parent_sections"] > 0

    # 3. Generate schedules (first call)
    sched_body = {
        "preferences": {"min_credits": "1", "max_credits": "4", "preferred_subjects": ["CPSC"]},
        "locked_ref_nos": [],
        "max_results": 50,
    }
    resp1 = client.post(f"/api/session/{sid}/schedules", json=sched_body)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert "search_metadata" in data1
    assert "status" in data1

    # 4. Repeat and verify deterministic IDs
    resp2 = client.post(f"/api/session/{sid}/schedules", json=sched_body)
    data2 = resp2.json()
    if data1.get("status") == "schedules_found" and data1.get("top_schedules"):
        ids1 = [s["schedule_id"] for s in data1["top_schedules"]]
        ids2 = [s["schedule_id"] for s in data2["top_schedules"]]
        assert ids1 == ids2, "Schedule IDs not deterministic"
        # Verify scores are non-increasing
        scores = [s["score"] for s in data1["top_schedules"]]
        assert scores == sorted(scores, reverse=True)

    # 5. Delete session
    resp = client.delete(f"/api/session/{sid}")
    assert resp.status_code == 204
    assert client.get(f"/api/session/{sid}").status_code == 404
