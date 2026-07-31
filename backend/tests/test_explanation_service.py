"""Tests for explanation_service.py — Stage 7 acceptance criteria.

Uses fake providers; no real API calls.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import time
from decimal import Decimal

import pytest

from app.core.explainer import ExplainerInput
from app.explanation_service import ExplanationResult, ExplanationService
from app.models import (
    CourseSection,
    MeetingTime,
    Preferences,
    RankedSchedule,
    Schedule,
)


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class _GoodProvider:
    async def explain(self, explainer_input: ExplainerInput) -> str:
        return "This schedule includes CPSC 031. It addresses the CS Major category."


class _FailingProvider:
    async def explain(self, explainer_input: ExplainerInput) -> str:
        raise RuntimeError("API unavailable")


class _SlowProvider:
    async def explain(self, explainer_input: ExplainerInput) -> str:
        await asyncio.sleep(9999)
        return ""


class _InvalidOutputProvider:
    """Returns text with an unknown course code."""

    async def explain(self, explainer_input: ExplainerInput) -> str:
        return "This schedule also covers CPSC 999, an excellent elective."


class _RefusingProvider:
    async def explain(self, explainer_input: ExplainerInput) -> str:
        return "I cannot provide an explanation for this schedule."


class _PanicProvider:
    """Should never be called — used to verify provider is not called in certain paths."""

    async def explain(self, explainer_input: ExplainerInput) -> str:
        raise AssertionError("Provider must not be called in this test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mt(days: str, start: tuple[int, int], end: tuple[int, int]) -> MeetingTime:
    return MeetingTime(days=tuple(days), start=time(*start), end=time(*end))


def _section(ref_no: str, code: str) -> CourseSection:
    subj, num = code.split()
    return CourseSection(
        ref_no=ref_no,
        subject=subj,
        number=num,
        section_id="01",
        title="A Course",
        credits=Decimal("1"),
        distribution=frozenset(),
        enr_limit=None,
        instructors=[],
        course_type="Course",
        meeting_times=[_mt("TR", (11, 20), (12, 35))],
        note="",
        linked_sections=[],
    )


def _make_ranked(code: str = "CPSC 031") -> RankedSchedule:
    s = _section("1", code)
    sched = Schedule(
        parent_sections=[s],
        lab_sections=[],
        total_credits=Decimal("1"),
    )
    return RankedSchedule(
        schedule=sched,
        category="balanced",
        score=100.0,
        score_breakdown={
            "requirement_gains": 100.0,
            "preferred_subjects": 0.0,
            "free_days": 0.0,
            "compactness": 0.0,
            "credit_load": 0.0,
        },
        requirement_gains=[],
        explanation="",
    )


_SCHEDULE_ID = "abc1234567890123"
_PREFS = Preferences(free_days=["F"])


# ---------------------------------------------------------------------------
# 1: No provider configured → fallback
# ---------------------------------------------------------------------------


async def test_no_provider_returns_fallback() -> None:
    """Test 1: With no provider, source is 'fallback'."""
    service = ExplanationService(provider=None)
    result = await service.explain(
        _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
    )
    assert result.source == "fallback"
    assert isinstance(result.text, str)
    assert len(result.text) > 0


# ---------------------------------------------------------------------------
# 2: Provider configured and valid → provider source
# ---------------------------------------------------------------------------


async def test_valid_provider_returns_provider_source() -> None:
    """Test 2: A good provider returns source='provider'."""
    service = ExplanationService(provider=_GoodProvider(), provider_name="fake")
    result = await service.explain(
        _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
    )
    assert result.source == "provider"
    assert "CPSC 031" in result.text


# ---------------------------------------------------------------------------
# 3: Provider failure → fallback
# ---------------------------------------------------------------------------


async def test_provider_failure_returns_fallback() -> None:
    """Test 3: RuntimeError from provider falls back to deterministic text."""
    service = ExplanationService(provider=_FailingProvider(), provider_name="fake")
    result = await service.explain(
        _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
    )
    assert result.source == "fallback"


# ---------------------------------------------------------------------------
# 4: Validation failure → fallback
# ---------------------------------------------------------------------------


async def test_invalid_provider_output_falls_back() -> None:
    """Test 4: Invalid provider output (unknown course code) falls back."""
    service = ExplanationService(provider=_InvalidOutputProvider(), provider_name="fake")
    result = await service.explain(
        _make_ranked("CPSC 031"),  # CPSC 999 not in schedule
        _PREFS,
        schedule_id=_SCHEDULE_ID,
        solver_cap_reached=False,
    )
    assert result.source == "fallback"


# ---------------------------------------------------------------------------
# 5: Deterministic schedule ID used
# ---------------------------------------------------------------------------


async def test_same_schedule_id_used_in_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 5: The schedule_id kwarg is passed through to ExplainerInput."""
    captured: list[ExplainerInput] = []

    class _CapturingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            captured.append(inp)
            return "CPSC 031 is scheduled."

    service = ExplanationService(provider=_CapturingProvider(), provider_name="fake")
    custom_id = "custom00id0001234"
    await service.explain(
        _make_ranked(), _PREFS, schedule_id=custom_id, solver_cap_reached=False
    )
    assert len(captured) == 1
    assert captured[0].schedule_id == custom_id


# ---------------------------------------------------------------------------
# 6: Cap flag reaches explanation input
# ---------------------------------------------------------------------------


async def test_cap_flag_reaches_input() -> None:
    """Test 6: solver_cap_reached=True is reflected in ExplainerInput."""
    captured: list[ExplainerInput] = []

    class _CapturingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            captured.append(inp)
            return "CPSC 031 is scheduled."

    service = ExplanationService(provider=_CapturingProvider(), provider_name="fake")
    await service.explain(
        _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=True
    )
    assert captured[0].solver_cap_reached is True


# ---------------------------------------------------------------------------
# 7: Preferences reach input
# ---------------------------------------------------------------------------


async def test_preferences_reach_input() -> None:
    """Test 7: Preferences (free_days) are included in ExplainerInput."""
    captured: list[ExplainerInput] = []

    class _CapturingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            captured.append(inp)
            return "CPSC 031 is scheduled."

    service = ExplanationService(provider=_CapturingProvider(), provider_name="fake")
    prefs = Preferences(free_days=["W", "F"])
    await service.explain(
        _make_ranked(), prefs, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
    )
    assert captured[0].free_days == ("W", "F")


# ---------------------------------------------------------------------------
# 8: Domain inputs not mutated
# ---------------------------------------------------------------------------


async def test_domain_inputs_not_mutated() -> None:
    """Test 8: explain() does not mutate RankedSchedule or Preferences."""
    rs = _make_ranked()
    prefs = Preferences(free_days=["F"])
    original_explanation = rs.explanation
    original_free_days = list(prefs.free_days)
    service = ExplanationService(provider=_GoodProvider(), provider_name="fake")
    await service.explain(rs, prefs, schedule_id=_SCHEDULE_ID, solver_cap_reached=False)
    assert rs.explanation == original_explanation
    assert prefs.free_days == original_free_days


# ---------------------------------------------------------------------------
# 9: Safe logging categories only
# ---------------------------------------------------------------------------


async def test_provider_failure_logs_safe_category(caplog: pytest.LogCaptureFixture) -> None:
    """Test 9: Provider failure logs only 'explanation_provider_unavailable'."""
    service = ExplanationService(provider=_FailingProvider(), provider_name="fake")
    with caplog.at_level(logging.WARNING, logger="app.explanation_service"):
        await service.explain(
            _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
        )
    messages = " ".join(caplog.messages)
    assert "explanation_provider_unavailable" in messages
    # No exception message text should be logged.
    assert "API unavailable" not in messages


async def test_timeout_logs_safe_category(caplog: pytest.LogCaptureFixture) -> None:
    """Test 9b: Timeout logs only 'explanation_provider_timeout'."""
    service = ExplanationService(
        provider=_SlowProvider(), provider_name="fake", timeout_seconds=0.05
    )
    with caplog.at_level(logging.WARNING, logger="app.explanation_service"):
        await service.explain(
            _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
        )
    messages = " ".join(caplog.messages)
    assert "explanation_provider_timeout" in messages


# ---------------------------------------------------------------------------
# 10: No audit anchors in logs
# ---------------------------------------------------------------------------


async def test_no_audit_anchor_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Test 10: Log output contains no synthetic fixture student name or ID."""
    service = ExplanationService(provider=_FailingProvider(), provider_name="fake")
    with caplog.at_level(logging.DEBUG, logger="app.explanation_service"):
        await service.explain(
            _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
        )
    log_text = " ".join(caplog.messages)
    assert "Student, Demo" not in log_text
    assert "000000000" not in log_text


# ---------------------------------------------------------------------------
# 11: Cache behavior
# ---------------------------------------------------------------------------


async def test_cache_hit_avoids_second_provider_call() -> None:
    """Test 11: Second request with same schedule_id hits the cache."""
    call_count = 0

    class _CountingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            nonlocal call_count
            call_count += 1
            return "CPSC 031 is scheduled."

    service = ExplanationService(
        provider=_CountingProvider(), provider_name="fake", cache_size=10
    )
    rs = _make_ranked()
    await service.explain(rs, _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False)
    await service.explain(rs, _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False)
    assert call_count == 1  # provider called only once


async def test_cache_different_id_calls_provider_again() -> None:
    """Test 12: Different schedule_id is a cache miss; provider called again."""
    call_count = 0

    class _CountingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            nonlocal call_count
            call_count += 1
            return "CPSC 031 is scheduled."

    service = ExplanationService(
        provider=_CountingProvider(), provider_name="fake", cache_size=10
    )
    rs = _make_ranked()
    await service.explain(rs, _PREFS, schedule_id="id_one_1234567", solver_cap_reached=False)
    await service.explain(rs, _PREFS, schedule_id="id_two_1234567", solver_cap_reached=False)
    assert call_count == 2


async def test_cache_eviction() -> None:
    """Test 13: LRU eviction works when max_size is exceeded."""
    call_count = 0

    class _CountingProvider:
        async def explain(self, inp: ExplainerInput) -> str:
            nonlocal call_count
            call_count += 1
            return "CPSC 031 is scheduled."

    service = ExplanationService(
        provider=_CountingProvider(), provider_name="fake", cache_size=2
    )
    rs = _make_ranked()
    # Fill cache with two entries.
    await service.explain(rs, _PREFS, schedule_id="id_1_1234567", solver_cap_reached=False)
    await service.explain(rs, _PREFS, schedule_id="id_2_1234567", solver_cap_reached=False)
    assert service.cache_size == 2
    # Adding a third evicts the LRU (id_1).
    await service.explain(rs, _PREFS, schedule_id="id_3_1234567", solver_cap_reached=False)
    assert service.cache_size == 2
    assert call_count == 3
    # Re-requesting id_1 is a cache miss now.
    await service.explain(rs, _PREFS, schedule_id="id_1_1234567", solver_cap_reached=False)
    assert call_count == 4


# ---------------------------------------------------------------------------
# 14: Refusal falls back
# ---------------------------------------------------------------------------


async def test_refusal_falls_back() -> None:
    """Test 14: Provider refusal text fails validation and falls back."""
    service = ExplanationService(provider=_RefusingProvider(), provider_name="fake")
    result = await service.explain(
        _make_ranked(), _PREFS, schedule_id=_SCHEDULE_ID, solver_cap_reached=False
    )
    assert result.source == "fallback"


# ---------------------------------------------------------------------------
# 15: Fallback is deterministic for repeated calls
# ---------------------------------------------------------------------------


async def test_fallback_deterministic_across_calls() -> None:
    """Test 15: Repeated fallback calls (no provider) return identical text."""
    service = ExplanationService(provider=None)
    rs = _make_ranked()
    results = [
        await service.explain(
            rs, _PREFS, schedule_id=f"unique_{i}_{i:08d}", solver_cap_reached=False
        )
        for i in range(3)
    ]
    # All should be fallback.
    assert all(r.source == "fallback" for r in results)
    # All should have identical text (same ranked schedule, same prefs).
    assert len({r.text for r in results}) == 1
