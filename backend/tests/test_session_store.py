"""Tests for app/session_store.py: session lifecycle, TTL, and thread safety."""
from __future__ import annotations

import threading
import time as _time
from datetime import datetime, timedelta, timezone

import pytest

from app.session_store import InMemorySessionStore, PlanningSession, SESSION_TTL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(hours_ago: float = 0) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)


# ---------------------------------------------------------------------------
# 1. Session creation produces opaque unique IDs
# ---------------------------------------------------------------------------


def test_session_ids_are_unique() -> None:
    """Test 1: Multiple creates produce distinct session IDs."""
    store = InMemorySessionStore()
    ids = {store.create().session_id for _ in range(20)}
    assert len(ids) == 20


def test_session_ids_are_opaque() -> None:
    """Test 1b: Session IDs do not encode recognisable student data."""
    store = InMemorySessionStore()
    session = store.create()
    assert "Demo" not in session.session_id
    assert "2027" not in session.session_id
    assert len(session.session_id) >= 20  # tokens have sufficient entropy


# ---------------------------------------------------------------------------
# 2. Session retrieval
# ---------------------------------------------------------------------------


def test_get_returns_created_session() -> None:
    """Test 2: get() returns the same session that was created."""
    store = InMemorySessionStore()
    created = store.create()
    fetched = store.get(created.session_id)
    assert fetched is not None
    assert fetched.session_id == created.session_id


# ---------------------------------------------------------------------------
# 3. Missing session returns None
# ---------------------------------------------------------------------------


def test_get_unknown_session_returns_none() -> None:
    """Test 3: get() returns None for an unknown session ID."""
    store = InMemorySessionStore()
    assert store.get("nonexistent-id-xyz") is None


# ---------------------------------------------------------------------------
# 4. Update persists state
# ---------------------------------------------------------------------------


def test_update_persists_parser_warnings() -> None:
    """Test 4: Updating a session's parser_warnings is retrievable."""
    store = InMemorySessionStore()
    session = store.create()
    session.parser_warnings = ["Item A unmatched"]
    store.update(session)
    fetched = store.get(session.session_id)
    assert fetched is not None
    assert fetched.parser_warnings == ["Item A unmatched"]


# ---------------------------------------------------------------------------
# 5. Delete removes session
# ---------------------------------------------------------------------------


def test_delete_removes_session() -> None:
    """Test 5: After delete(), get() returns None."""
    store = InMemorySessionStore()
    session = store.create()
    assert store.delete(session.session_id) is True
    assert store.get(session.session_id) is None


def test_delete_nonexistent_returns_false() -> None:
    """Test 5b: Deleting a nonexistent session returns False."""
    store = InMemorySessionStore()
    assert store.delete("ghost-id") is False


# ---------------------------------------------------------------------------
# 6. Expired session is unavailable
# ---------------------------------------------------------------------------


def test_expired_session_returns_none() -> None:
    """Test 6: get() returns None for a session that has exceeded its TTL."""
    store = InMemorySessionStore(ttl=timedelta(seconds=0))
    session = store.create()
    # TTL=0 means immediately expired
    fetched = store.get(session.session_id)
    assert fetched is None


def test_non_expired_session_accessible() -> None:
    """Test 6b: A freshly created session is accessible."""
    store = InMemorySessionStore(ttl=timedelta(hours=2))
    session = store.create()
    assert store.get(session.session_id) is not None


# ---------------------------------------------------------------------------
# 7. Access refreshes last_accessed_at
# ---------------------------------------------------------------------------


def test_get_refreshes_last_accessed_at() -> None:
    """Test 7: Accessing a session updates its last_accessed_at."""
    store = InMemorySessionStore()
    session = store.create()
    original_accessed = session.last_accessed_at
    _time.sleep(0.01)
    fetched = store.get(session.session_id)
    assert fetched is not None
    assert fetched.last_accessed_at > original_accessed


# ---------------------------------------------------------------------------
# 8. purge_expired() returns correct count
# ---------------------------------------------------------------------------


def test_purge_expired_returns_count() -> None:
    """Test 8: purge_expired() removes expired sessions and returns the count."""
    store = InMemorySessionStore(ttl=timedelta(seconds=0))
    store.create()
    store.create()
    store.create()
    count = store.purge_expired()
    assert count == 3
    assert store.session_count == 0


def test_purge_expired_leaves_valid_sessions() -> None:
    """Test 8b: purge_expired() does not remove unexpired sessions."""
    store = InMemorySessionStore(ttl=timedelta(hours=2))
    store.create()
    store.create()
    count = store.purge_expired()
    assert count == 0
    assert store.session_count == 2


# ---------------------------------------------------------------------------
# 9. UTC-aware timestamps
# ---------------------------------------------------------------------------


def test_created_at_is_utc_aware() -> None:
    """Test 9: created_at is timezone-aware UTC."""
    store = InMemorySessionStore()
    session = store.create()
    assert session.created_at.tzinfo is not None
    assert session.created_at.tzinfo.utcoffset(session.created_at) is not None


def test_last_accessed_at_is_utc_aware() -> None:
    """Test 9b: last_accessed_at is timezone-aware UTC."""
    store = InMemorySessionStore()
    session = store.create()
    assert session.last_accessed_at.tzinfo is not None


# ---------------------------------------------------------------------------
# 10. Raw bytes not retained
# ---------------------------------------------------------------------------


def test_session_has_no_bytes_field() -> None:
    """Test 10: PlanningSession dataclass has no field for raw file bytes."""
    session = PlanningSession(
        session_id="test",
        created_at=datetime.now(tz=timezone.utc),
        last_accessed_at=datetime.now(tz=timezone.utc),
    )
    d = session.__dict__
    for key in d:
        assert "bytes" not in key.lower()
        assert "raw" not in key.lower()
        assert "pdf" not in key.lower()
        assert "csv" not in key.lower()


# ---------------------------------------------------------------------------
# 11. Concurrent operations do not corrupt state
# ---------------------------------------------------------------------------


def test_concurrent_creates_do_not_corrupt() -> None:
    """Test 11: Concurrent session creation is thread-safe."""
    store = InMemorySessionStore()
    ids: list[str] = []
    lock = threading.Lock()

    def _create() -> None:
        s = store.create()
        with lock:
            ids.append(s.session_id)

    threads = [threading.Thread(target=_create) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 50
    assert len(set(ids)) == 50  # all unique


def test_concurrent_get_and_delete_do_not_error() -> None:
    """Test 11b: Concurrent reads and deletes do not raise exceptions."""
    store = InMemorySessionStore()
    session = store.create()
    sid = session.session_id
    errors: list[Exception] = []

    def _reader() -> None:
        for _ in range(20):
            try:
                store.get(sid)
            except Exception as e:
                errors.append(e)

    def _deleter() -> None:
        try:
            store.delete(sid)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_reader) for _ in range(5)]
    threads.append(threading.Thread(target=_deleter))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


# ---------------------------------------------------------------------------
# 12. Test stores are isolated
# ---------------------------------------------------------------------------


def test_stores_are_isolated() -> None:
    """Test 12: Two separate InMemorySessionStore instances share no state."""
    store_a = InMemorySessionStore()
    store_b = InMemorySessionStore()
    session = store_a.create()
    assert store_b.get(session.session_id) is None


# ---------------------------------------------------------------------------
# 13. inputs_loaded reflects session state
# ---------------------------------------------------------------------------


def test_inputs_loaded_false_by_default() -> None:
    store = InMemorySessionStore()
    session = store.create()
    assert session.inputs_loaded is False


# ---------------------------------------------------------------------------
# 14. Custom now passed to purge
# ---------------------------------------------------------------------------


def test_purge_with_explicit_now() -> None:
    """purge_expired accepts an explicit 'now' datetime for deterministic testing."""
    store = InMemorySessionStore(ttl=timedelta(hours=1))
    store.create()
    future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
    count = store.purge_expired(now=future)
    assert count == 1
