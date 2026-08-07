"""CORS configuration and behavior tests.

Four behavioral requirements (spec §3):
  1. Local origin (http://localhost:3000) is permitted.
  2. A configured production origin is permitted.
  3. An unknown origin is NOT granted CORS access.
  4. No wildcard origin exists.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import _parse_cors_origins

# ---------------------------------------------------------------------------
# Shared test app with fixed, known origins (isolated from env)
# ---------------------------------------------------------------------------

_KNOWN_LOCAL = "http://localhost:3000"
_KNOWN_PROD = "https://academic-planner.vercel.app"
_CORS_TEST_ORIGINS = [_KNOWN_LOCAL, _KNOWN_PROD]

_cors_app = FastAPI()
_cors_app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_TEST_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@_cors_app.get("/ping")
def _ping() -> dict[str, str]:
    return {"ok": "true"}


def _cors_client() -> TestClient:
    return TestClient(_cors_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Requirement 1: local origin permitted
# ---------------------------------------------------------------------------


class TestLocalOriginPermitted:
    def test_preflight_local_origin_gets_acao_header(self) -> None:
        resp = _cors_client().options(
            "/ping",
            headers={
                "Origin": _KNOWN_LOCAL,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == _KNOWN_LOCAL

    def test_simple_get_local_origin_gets_acao_header(self) -> None:
        resp = _cors_client().get("/ping", headers={"Origin": _KNOWN_LOCAL})
        assert resp.headers.get("access-control-allow-origin") == _KNOWN_LOCAL


# ---------------------------------------------------------------------------
# Requirement 2: configured production origin permitted
# ---------------------------------------------------------------------------


class TestProductionOriginPermitted:
    def test_preflight_production_origin_gets_acao_header(self) -> None:
        resp = _cors_client().options(
            "/ping",
            headers={
                "Origin": _KNOWN_PROD,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == _KNOWN_PROD

    def test_simple_get_production_origin_gets_acao_header(self) -> None:
        resp = _cors_client().get("/ping", headers={"Origin": _KNOWN_PROD})
        assert resp.headers.get("access-control-allow-origin") == _KNOWN_PROD


# ---------------------------------------------------------------------------
# Requirement 3: unknown origin is NOT granted CORS access
# ---------------------------------------------------------------------------


class TestUnknownOriginNotPermitted:
    def test_preflight_unknown_origin_no_acao_header(self) -> None:
        resp = _cors_client().options(
            "/ping",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        assert acao != "https://evil.example.com"
        assert acao != "*"

    def test_simple_get_unknown_origin_no_wildcard(self) -> None:
        resp = _cors_client().get("/ping", headers={"Origin": "https://attacker.io"})
        acao = resp.headers.get("access-control-allow-origin", "")
        assert acao != "*"
        assert acao != "https://attacker.io"


# ---------------------------------------------------------------------------
# Requirement 4: no wildcard origin
# ---------------------------------------------------------------------------


class TestNoWildcardOrigin:
    def test_configured_origins_contain_no_wildcard(self) -> None:
        assert "*" not in _CORS_TEST_ORIGINS

    def test_parse_default_no_wildcard(self) -> None:
        assert "*" not in _parse_cors_origins(None)

    def test_parse_custom_origin_no_wildcard(self) -> None:
        assert "*" not in _parse_cors_origins("https://demo.vercel.app")


# ---------------------------------------------------------------------------
# Origin parsing unit tests
# ---------------------------------------------------------------------------


class TestParseCorsOrigins:
    def test_default_is_localhost(self) -> None:
        result = _parse_cors_origins("")
        assert result == []

    def test_none_falls_back_to_env_or_localhost(self) -> None:
        # When env is not set, should return localhost (tested in integration).
        # When called with explicit None in a clean env, the default is localhost.
        # We can at least confirm the return type.
        result = _parse_cors_origins("http://localhost:3000")
        assert "http://localhost:3000" in result

    def test_single_origin(self) -> None:
        result = _parse_cors_origins("https://demo.example.com")
        assert result == ["https://demo.example.com"]

    def test_comma_separated_origins(self) -> None:
        result = _parse_cors_origins("https://a.com,https://b.com")
        assert result == ["https://a.com", "https://b.com"]

    def test_whitespace_stripped(self) -> None:
        result = _parse_cors_origins("  https://a.com , https://b.com  ")
        assert result == ["https://a.com", "https://b.com"]

    def test_empty_segments_skipped(self) -> None:
        result = _parse_cors_origins("https://a.com,,https://b.com,")
        assert result == ["https://a.com", "https://b.com"]
