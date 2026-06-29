from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from mini_code_agent.agent.events import (
    AgentEvent,
    ModelStarted,
    RunStarted,
    RunStopped,
)
from mini_code_agent.agent.models import StopReason
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    SessionTraceLimits,
)
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.providers.base import TokenUsage


def trace_events(
    *,
    run_id: str = "run-1",
    error: str | None = None,
) -> tuple[AgentEvent, ...]:
    started_at = datetime.now(UTC) + timedelta(seconds=1)
    stop_reason = StopReason.COMPLETED if error is None else StopReason.PROVIDER_ERROR
    return (
        RunStarted(
            run_id=run_id,
            timestamp=started_at,
            max_turns=8,
        ),
        ModelStarted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=1),
            turn=1,
            request_id=f"{run_id}:1",
        ),
        RunStopped(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=2),
            turns=1,
            reason=stop_reason,
            usage=TokenUsage(input_tokens=10, output_tokens=2),
            error=error,
        ),
    )


def populated_store(
    database: Path,
    *,
    limits: SessionTraceLimits | None = None,
    secrets: tuple[str | SecretStr, ...] = (),
    events: tuple[AgentEvent, ...] | None = None,
) -> SqliteSessionTraceStore:
    store = SqliteSessionTraceStore(database, limits=limits, secrets=secrets)
    store.initialize()
    store.create_session("session-1")
    journal = store.journal("session-1")
    for event in events or trace_events():
        journal.append(event)
    return store


def test_read_trace_is_typed_bounded_and_ordered_and_verifies(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = populated_store(database)

    selected = store.read_trace("session-1", after_sequence=1, limit=2)
    verification = store.verify_trace("session-1")
    session = store.get_session("session-1")

    assert tuple(record.sequence for record in selected) == (2, 3)
    assert isinstance(selected[0].event, ModelStarted)
    assert isinstance(selected[1].event, RunStopped)
    assert verification.event_count == 3
    assert verification.trace_head_sha256 == session.trace_head_sha256

    store.close()
    with SqliteSessionTraceStore(database) as reopened:
        assert reopened.read_trace("session-1", limit=3) == (
            *reopened.read_trace("session-1", limit=2),
            reopened.read_trace("session-1", after_sequence=2, limit=1)[0],
        )


def test_empty_session_trace_verifies_to_zero_head(tmp_path: Path) -> None:
    with SqliteSessionTraceStore(tmp_path / "state.db") as store:
        store.create_session("session-1")

        verification = store.verify_trace("session-1")

    assert verification.event_count == 0
    assert verification.trace_head_sha256 == EMPTY_TRACE_SHA256


def test_trace_queries_reject_invalid_bounds_and_unknown_session(
    tmp_path: Path,
) -> None:
    limits = SessionTraceLimits(max_query_rows=2)
    with SqliteSessionTraceStore(
        tmp_path / "state.db",
        limits=limits,
    ) as store:
        store.create_session("session-1")

        with pytest.raises(PersistenceError) as negative:
            store.read_trace("session-1", after_sequence=-1, limit=1)
        with pytest.raises(PersistenceError) as zero:
            store.read_trace("session-1", limit=0)
        with pytest.raises(PersistenceError) as oversized:
            store.read_trace("session-1", limit=3)
        with pytest.raises(PersistenceError) as missing:
            store.verify_trace("missing-session")

    assert negative.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    assert zero.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    assert oversized.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    assert missing.value.code is PersistenceErrorCode.SESSION_NOT_FOUND


@pytest.mark.parametrize(
    ("sql", "parameters"),
    [
        (
            "UPDATE trace_events SET payload_json = ? WHERE sequence = 2",
            ('{"secret":"secret-tampered-payload"}',),
        ),
        (
            "UPDATE trace_events SET previous_sha256 = ? WHERE sequence = 2",
            ("f" * 64,),
        ),
        (
            "DELETE FROM trace_events WHERE sequence = 2",
            (),
        ),
        (
            "UPDATE sessions SET trace_head_sha256 = ? WHERE session_id = ?",
            ("f" * 64, "session-1"),
        ),
    ],
)
def test_verify_trace_detects_corruption_without_leaking_content(
    tmp_path: Path,
    sql: str,
    parameters: tuple[str, ...],
) -> None:
    database = tmp_path / "secret-state.db"
    store = populated_store(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(sql, parameters)

    with pytest.raises(PersistenceError) as captured:
        store.verify_trace("session-1")

    assert captured.value.code is PersistenceErrorCode.TRACE_CORRUPT
    assert "secret-tampered-payload" not in captured.value.public_message
    assert str(database) not in captured.value.public_message


def test_read_trace_rejects_malformed_event_payload(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = populated_store(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE trace_events SET payload_json = ? WHERE sequence = 2",
            ('{"type":"secret-unknown-event"}',),
        )

    with pytest.raises(PersistenceError) as captured:
        store.read_trace("session-1", limit=3)

    assert captured.value.code is PersistenceErrorCode.TRACE_CORRUPT
    assert "secret-unknown-event" not in captured.value.public_message


def test_configured_secret_is_scrubbed_before_hash_and_storage(
    tmp_path: Path,
) -> None:
    secret = "sk-secret-value-123456789"
    database = tmp_path / "state.db"
    store = populated_store(
        database,
        secrets=(SecretStr(secret),),
        events=trace_events(error=f"Provider failed with {secret}."),
    )

    records = store.read_trace("session-1", limit=3)
    verification = store.verify_trace("session-1")
    store.close()
    persisted_bytes = b"".join(
        path.read_bytes()
        for path in (
            database,
            database.with_name(f"{database.name}-wal"),
            database.with_name(f"{database.name}-shm"),
        )
        if path.exists()
    )

    stopped = records[-1].event
    assert isinstance(stopped, RunStopped)
    assert stopped.error == "Provider failed with ***."
    assert verification.event_count == 3
    assert secret.encode() not in persisted_bytes
