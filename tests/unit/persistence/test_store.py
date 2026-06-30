from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    SessionStatus,
    SessionTraceLimits,
)
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.repair.events import RepairStarted


def test_store_requires_explicit_initialization(tmp_path: Path) -> None:
    store = SqliteSessionTraceStore(tmp_path / "state.db")

    with pytest.raises(PersistenceError) as captured:
        store.create_session("session-1")

    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
    assert captured.value.public_message == "Session store is not initialized."


def test_create_and_get_session_with_generated_or_explicit_id(
    tmp_path: Path,
) -> None:
    store = SqliteSessionTraceStore(tmp_path / "state.db")
    store.initialize()

    explicit = store.create_session("session-1")
    generated = store.create_session()

    assert explicit.session_id == "session-1"
    assert explicit.status is SessionStatus.READY
    assert explicit.event_count == 0
    assert explicit.next_sequence == 1
    assert explicit.trace_head_sha256 == EMPTY_TRACE_SHA256
    assert store.get_session("session-1") == explicit
    assert re.fullmatch(r"[0-9a-f-]{36}", generated.session_id)


def test_duplicate_session_id_fails_without_mutation(tmp_path: Path) -> None:
    store = SqliteSessionTraceStore(tmp_path / "state.db")
    store.initialize()
    original = store.create_session("session-1")

    with pytest.raises(PersistenceError) as captured:
        store.create_session("session-1")

    assert captured.value.code is PersistenceErrorCode.SESSION_EXISTS
    assert store.get_session("session-1") == original


def test_unknown_and_invalid_session_ids_use_static_errors(
    tmp_path: Path,
) -> None:
    database = tmp_path / "secret-database.db"
    store = SqliteSessionTraceStore(database)
    store.initialize()

    with pytest.raises(PersistenceError) as missing:
        store.get_session("missing-session")
    with pytest.raises(PersistenceError) as invalid:
        store.get_session("../secret-session")

    assert missing.value.code is PersistenceErrorCode.SESSION_NOT_FOUND
    assert invalid.value.code is PersistenceErrorCode.INVALID_IDENTIFIER
    assert "missing-session" not in missing.value.public_message
    assert "secret-session" not in invalid.value.public_message
    assert str(database) not in str(missing.value)


def test_list_sessions_is_bounded_and_deterministic(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = SqliteSessionTraceStore(database)
    store.initialize()
    store.create_session("session-c")
    store.create_session("session-a")
    store.create_session("session-b")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?",
            ("2026-06-30T00:00:00+00:00", "2026-06-30T00:00:00+00:00"),
        )

    listed = store.list_sessions(limit=2)

    assert tuple(session.session_id for session in listed) == (
        "session-a",
        "session-b",
    )
    with pytest.raises(PersistenceError) as zero:
        store.list_sessions(limit=0)
    with pytest.raises(PersistenceError) as oversized:
        store.list_sessions(limit=1_001)
    assert zero.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    assert oversized.value.code is PersistenceErrorCode.LIMIT_EXCEEDED


def test_context_manager_closes_and_reopen_preserves_sessions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    with SqliteSessionTraceStore(database) as store:
        created = store.create_session("session-1")

    with pytest.raises(PersistenceError):
        store.get_session("session-1")
    with SqliteSessionTraceStore(database) as reopened:
        assert reopened.get_session("session-1") == created


def test_run_queries_are_bounded_and_require_existing_session(
    tmp_path: Path,
) -> None:
    store = SqliteSessionTraceStore(
        tmp_path / "state.db",
        limits=SessionTraceLimits(max_query_rows=3),
    )
    store.initialize()
    store.create_session("session-1")

    assert store.list_runs("session-1", limit=3) == ()
    with pytest.raises(PersistenceError) as missing_run:
        store.get_run("session-1", "run-1")
    with pytest.raises(PersistenceError) as missing_session:
        store.list_runs("missing", limit=1)
    with pytest.raises(PersistenceError) as oversized:
        store.list_runs("session-1", limit=4)

    assert missing_run.value.code is PersistenceErrorCode.RUN_NOT_FOUND
    assert missing_session.value.code is PersistenceErrorCode.SESSION_NOT_FOUND
    assert oversized.value.code is PersistenceErrorCode.LIMIT_EXCEEDED


def test_malformed_database_row_is_normalized_without_raw_content(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = SqliteSessionTraceStore(database)
    store.initialize()
    store.create_session("session-1")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE sessions SET created_at = ? WHERE session_id = ?",
            ("secret-invalid-timestamp", "session-1"),
        )

    with pytest.raises(PersistenceError) as captured:
        store.get_session("session-1")

    assert captured.value.code is PersistenceErrorCode.TRACE_CORRUPT
    assert "secret-invalid-timestamp" not in captured.value.public_message


def test_store_exposes_repair_journal_queries_and_verification(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    with SqliteSessionTraceStore(database) as store:
        event = RepairStarted(
            repair_id="repair-1",
            timestamp=datetime.now(UTC),
            scope_sha256="a" * 64,
            max_attempts=3,
            test_target_count=1,
            editable_path_count=1,
        )
        store.repair_journal().append(event)

        run = store.get_repair_run("repair-1")
        records = store.read_repair_trace("repair-1")
        verification = store.verify_repair_trace("repair-1")

    assert run.repair_id == "repair-1"
    assert tuple(record.event for record in records) == (event,)
    assert verification.event_count == 1


def test_repair_store_accessors_require_initialization(tmp_path: Path) -> None:
    store = SqliteSessionTraceStore(tmp_path / "state.db")

    with pytest.raises(PersistenceError) as journal_error:
        store.repair_journal()
    with pytest.raises(PersistenceError) as read_error:
        store.read_repair_trace("repair-1")

    assert journal_error.value.code is PersistenceErrorCode.STORAGE_FAILED
    assert read_error.value.code is PersistenceErrorCode.STORAGE_FAILED
