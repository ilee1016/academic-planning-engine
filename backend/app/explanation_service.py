"""Explanation service: coordinates input construction, provider calls, and caching.

Responsibility:
  - Build sanitized ExplainerInput from a RankedSchedule + Preferences.
  - If a provider is configured, call it and validate its output.
  - Fall back to deterministic explanation on any provider failure.
  - Cache results to avoid redundant provider calls for the same schedule.
  - Log only sanitized error categories; never log prompts, responses, or
    student data.

Public API:
    ExplanationResult
    ExplanationService
    (factory load_explanation_service in main.py reads env vars and calls
     load_provider + ExplanationService)

Invariants:
    INV-AI-04  Provider failure always falls back; never propagates as API error.
    INV-AI-05  Fallback is deterministic.
    INV-AI-09  Provider calls occur only through this service, never in routes.
    INV-AI-10  Logs contain no prompt text, response text, audit excerpts, or identity.
    INV-AI-11  Provider configuration secrets are not stored here.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from app.core.explainer import (
    ExplainerInput,
    ExplanationValidationError,
    build_explainer_input,
    generate_fallback_explanation,
    validate_explanation,
)
from app.explanation_provider import AnthropicProvider, ExplanationProvider
from app.models import Preferences, RankedSchedule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExplanationResult:
    """The explanation text and the source that produced it."""

    text: str
    source: Literal["provider", "fallback"]


# ---------------------------------------------------------------------------
# Bounded LRU cache
# ---------------------------------------------------------------------------

_CacheKey = tuple[str, str, str]  # (schedule_id, provider_name, prompt_version)


class _BoundedCache:
    """Simple LRU cache for ExplanationResult objects.

    Cache contains only explanation text and source; no StudentRecord,
    audit content, provider prompts, or raw provider responses.
    """

    def __init__(self, max_size: int) -> None:
        self._data: OrderedDict[_CacheKey, ExplanationResult] = OrderedDict()
        self._max_size = max_size

    def get(self, key: _CacheKey) -> ExplanationResult | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: _CacheKey, result: ExplanationResult) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = result
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    @property
    def size(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# Explanation service
# ---------------------------------------------------------------------------

_DEFAULT_PROMPT_VERSION = "1"


class ExplanationService:
    """Coordinates explainer input construction, provider invocation, and fallback.

    The provider (if any) is called with asyncio.wait_for to enforce the
    configured timeout.  Any exception — timeout, network error, invalid
    output, or unexpected failure — silently falls back to the deterministic
    explanation.  Provider errors are logged as sanitized category strings
    only; no prompt text, response text, or exception messages are logged.

    Args:
        provider:        Concrete provider instance, or None for fallback-only mode.
        provider_name:   Name string for the cache key (e.g. "anthropic" or "none").
        cache_size:      Maximum number of cached results (LRU eviction).
        timeout_seconds: Per-call timeout for provider requests.
    """

    def __init__(
        self,
        provider: ExplanationProvider | None = None,
        provider_name: str = "none",
        cache_size: int = 500,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._prompt_version = _DEFAULT_PROMPT_VERSION
        self._timeout = timeout_seconds
        self._cache = _BoundedCache(cache_size)

    @property
    def cache_size(self) -> int:
        return self._cache.size

    async def explain(
        self,
        ranked_schedule: RankedSchedule,
        preferences: Preferences,
        *,
        schedule_id: str,
        solver_cap_reached: bool,
    ) -> ExplanationResult:
        """Return an explanation for the given ranked schedule.

        Steps:
        1. Build sanitized ExplainerInput (never mutates inputs).
        2. Check cache.
        3. If no provider: return deterministic fallback.
        4. Call provider with timeout; validate output.
        5. On any failure: log category, return deterministic fallback.
        """
        explainer_input = build_explainer_input(
            ranked_schedule,
            preferences,
            schedule_id=schedule_id,
            solver_cap_reached=solver_cap_reached,
        )

        cache_key: _CacheKey = (
            schedule_id,
            self._provider_name,
            self._prompt_version,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if self._provider is None:
            result = ExplanationResult(
                text=generate_fallback_explanation(explainer_input),
                source="fallback",
            )
            self._cache.put(cache_key, result)
            return result

        result = await self._call_provider(explainer_input, cache_key)
        return result

    async def _call_provider(
        self,
        explainer_input: ExplainerInput,
        cache_key: _CacheKey,
    ) -> ExplanationResult:
        """Invoke provider, validate, and return result or fallback."""
        assert self._provider is not None

        try:
            raw = await asyncio.wait_for(
                self._provider.explain(explainer_input),
                timeout=self._timeout,
            )
            text = validate_explanation(raw, explainer_input)
            result = ExplanationResult(text=text, source="provider")
            self._cache.put(cache_key, result)
            return result

        except asyncio.TimeoutError:
            logger.warning("explanation_provider_timeout")
        except ExplanationValidationError:
            logger.warning("explanation_provider_invalid_output")
        except Exception:
            logger.warning("explanation_provider_unavailable")

        # Fallback on any provider failure.
        fallback_text = generate_fallback_explanation(explainer_input)
        result = ExplanationResult(text=fallback_text, source="fallback")
        self._cache.put(cache_key, result)
        return result
