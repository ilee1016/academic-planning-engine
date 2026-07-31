"""In-memory session store for the Academic Planning Engine.

Sessions hold parsed domain state for one student's planning request.
Raw file bytes and audit text are never retained after parsing.

Public API:
    PlanningSession
    InMemorySessionStore
    SESSION_TTL

Privacy invariants:
    INV-API-01: Raw upload bytes are never stored.
    INV-API-02: Raw audit text is never stored.
    INV-API-03: StudentRecord is stored in-memory only; name/id never leave the process
                through API responses (enforced in api_models.py converters).

Concurrency:
    InMemorySessionStore uses threading.Lock for mutations.  Safe for FastAPI's
    single-process, multi-threaded default deployment mode.  Each server worker
    process has its own independent store; no cross-process sharing occurs in MVP.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models import (
    CourseSection,
    Preferences,
    RankedSchedule,
    RequirementStatus,
    StudentRecord,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TTL: timedelta = timedelta(hours=2)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class PlanningSession:
    """All state scoped to one upload-and-plan interaction.

    Fields that must NOT be set here:
      - Raw PDF bytes
      - Raw CSV bytes
      - Extracted audit text
    They are discarded immediately after parsing in the route handler.
    """

    session_id: str
    created_at: datetime           # UTC-aware
    last_accessed_at: datetime     # UTC-aware; refreshed on each GET/POST access

    # Populated by POST /api/session/{id}/inputs
    student: StudentRecord | None = None
    catalog_sections: list[CourseSection] | None = None
    requirement_status: RequirementStatus | None = None
    preferences: Preferences | None = None
    parser_warnings: list[str] = field(default_factory=list)

    # Populated by POST /api/session/{id}/schedules; cleared on new input upload.
    # Used by the explanation endpoint to look up schedule_id → RankedSchedule.
    latest_ranked_schedules: tuple[RankedSchedule, ...] = ()
    latest_search_cap_reached: bool = False
    latest_preferences: Preferences | None = None

    @property
    def inputs_loaded(self) -> bool:
        return (
            self.student is not None
            and self.catalog_sections is not None
            and self.requirement_status is not None
        )

    def is_expired(self, now: datetime | None = None, ttl: timedelta = SESSION_TTL) -> bool:
        ref = now if now is not None else datetime.now(tz=timezone.utc)
        return (ref - self.created_at) > ttl

    def touch(self, now: datetime | None = None) -> None:
        self.last_accessed_at = now if now is not None else datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class InMemorySessionStore:
    """Thread-safe, in-memory session registry.

    The store does NOT run a background cleanup worker.
    Call purge_expired() at startup or periodically from the application.

    INV-API-04: Session IDs are opaque.  Generated via secrets.token_urlsafe().
    INV-API-05: Expired sessions are inaccessible (get() returns None for them).
    """

    def __init__(self, ttl: timedelta = SESSION_TTL) -> None:
        self._sessions: dict[str, PlanningSession] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def create(self, now: datetime | None = None) -> PlanningSession:
        """Create and register a new session; return it."""
        ts = now if now is not None else datetime.now(tz=timezone.utc)
        session_id = secrets.token_urlsafe(32)
        session = PlanningSession(
            session_id=session_id,
            created_at=ts,
            last_accessed_at=ts,
        )
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str, now: datetime | None = None) -> PlanningSession | None:
        """Return a non-expired session, or None if missing or expired."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired(now, self._ttl):
                # Remove silently; treat as not found.
                del self._sessions[session_id]
                return None
            session.touch(now)
            return session

    def update(self, session: PlanningSession) -> None:
        """Persist an updated session object back into the store."""
        with self._lock:
            self._sessions[session.session_id] = session

    def delete(self, session_id: str) -> bool:
        """Remove a session.  Return True if it existed."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def purge_expired(self, now: datetime | None = None) -> int:
        """Remove all expired sessions.  Return the count of removed sessions."""
        ref = now if now is not None else datetime.now(tz=timezone.utc)
        to_remove = []
        with self._lock:
            for sid, session in self._sessions.items():
                if session.is_expired(ref, self._ttl):
                    to_remove.append(sid)
            for sid in to_remove:
                del self._sessions[sid]
        return len(to_remove)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)
