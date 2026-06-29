from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from mini_code_agent.agent.events import (
    AgentEvent,
    ModelCompleted,
    ModelStarted,
    RunStarted,
    RunStopped,
    ToolCompleted,
    ToolStarted,
)
from mini_code_agent.agent.models import StopReason
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    RunStatus,
    SessionStatus,
    SessionTraceLimits,
)
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.providers.base import FinishReason, TokenUsage
from mini_code_agent.tools.base import SideEffect


def lifecycle(run_id: str = "run-1") -> tuple[AgentEvent, ...]:
    started_at = datetime.now(UTC) + timedelta(seconds=1)
    return (
        RunStarted(run_id=run_id, timestamp=started_at, max_turns=8),
        ModelStarted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=1),
            turn=1,
            request_id=f"{run_id}:1",
        ),
        ModelCompleted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=2),
            turn=1,
            finish_reason=FinishReason.TOOL_CALL,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        ),
        ToolStarted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=3),
            turn=1,
            tool_call_id="call-1",
            tool_name="read_file",
            side_effect=SideEffect.READ_ONLY,
        ),
        ToolCompleted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=4),
            turn=1,
            tool_call_id="call-1",
            tool_name="read_file",
            is_error=False,
        ),
        ModelStarted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=5),
            turn=2,
            request_id=f"{run_id}:2",
        ),
        ModelCompleted(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=6),
            turn=2,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=20, output_tokens=6),
        ),
        RunStopped(
            run_id=run_id,
            timestamp=started_at + timedelta(milliseconds=7),
            turns=2,
            reason=StopReason.COMPLETED,
            tool_calls=1,
            usage=TokenUsage(input_tokens=30, output_tokens=11),
        ),
    )


def raw_trace(database: Path) -> tuple[sqlite3.Row, ...]:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.row_factory = sqlite3.Row
        return tuple(connection.execute("SELECT * FROM trace_events ORDER BY sequence").fetchall())


def test_journal_appends_lifecycle_and_updates_projections_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        journal = store.journal("session-1")
        for event in lifecycle():
            journal.append(event)

        session = store.get_session("session-1")
        run = store.get_run("session-1", "run-1")

    rows = raw_trace(database)
    assert tuple(row["event_type"] for row in rows) == (
        "run_started",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "model_started",
        "model_completed",
        "run_stopped",
    )
    assert tuple(row["sequence"] for row in rows) == tuple(range(1, 9))
    assert rows[0]["previous_sha256"] == EMPTY_TRACE_SHA256
    assert all(
        current["previous_sha256"] == previous["event_sha256"]
        for previous, current in pairwise(rows)
    )
    assert session.status is SessionStatus.COMPLETED
    assert session.event_count == 8
    assert session.next_sequence == 9
    assert session.trace_head_sha256 == rows[-1]["event_sha256"]
    assert run.status is RunStatus.COMPLETED
    assert run.stop_reason is StopReason.COMPLETED
    assert run.turns == 2
    assert run.tool_calls == 1
    assert run.input_tokens == 30
    assert run.output_tokens == 11


def test_same_event_is_idempotent_but_conflicting_event_id_is_rejected(
    tmp_path: Path,
) -> None:
    with SqliteSessionTraceStore(tmp_path / "state.db") as store:
        store.create_session("session-1")
        journal = store.journal("session-1")
        original = lifecycle()[0]
        journal.append(original)
        journal.append(original)
        conflicting = RunStarted(
            event_id=original.event_id,
            run_id="different-run",
            timestamp=original.timestamp,
            max_turns=2,
        )

        with pytest.raises(PersistenceError) as captured:
            journal.append(conflicting)

        assert captured.value.code is PersistenceErrorCode.EVENT_CONFLICT
        assert store.get_session("session-1").event_count == 1


def test_journal_rejects_invalid_run_transitions_and_cross_session_use(
    tmp_path: Path,
) -> None:
    with SqliteSessionTraceStore(tmp_path / "state.db") as store:
        store.create_session("session-1")
        store.create_session("session-2")
        first = store.journal("session-1")
        second = store.journal("session-2")
        events = lifecycle()

        with pytest.raises(PersistenceError) as before_start:
            first.append(events[1])
        first.append(events[0])
        with pytest.raises(PersistenceError) as duplicate_run:
            first.append(
                RunStarted(
                    run_id="run-1",
                    timestamp=events[0].timestamp + timedelta(milliseconds=1),
                    max_turns=8,
                )
            )
        with pytest.raises(PersistenceError) as cross_session:
            second.append(events[1])
        for event in events[1:]:
            first.append(event)
        with pytest.raises(PersistenceError) as after_stop:
            first.append(
                ModelStarted(
                    run_id="run-1",
                    timestamp=events[-1].timestamp + timedelta(milliseconds=1),
                    turn=3,
                    request_id="run-1:3",
                )
            )

    assert before_start.value.code is PersistenceErrorCode.INVALID_TRANSITION
    assert duplicate_run.value.code is PersistenceErrorCode.RUN_CONFLICT
    assert cross_session.value.code is PersistenceErrorCode.INVALID_TRANSITION
    assert after_stop.value.code is PersistenceErrorCode.INVALID_TRANSITION


def test_journal_requires_existing_session(tmp_path: Path) -> None:
    with (
        SqliteSessionTraceStore(tmp_path / "state.db") as store,
        pytest.raises(PersistenceError) as captured,
    ):
        store.journal("missing-session")

    assert captured.value.code is PersistenceErrorCode.SESSION_NOT_FOUND


def test_event_count_limit_fails_without_partial_append(tmp_path: Path) -> None:
    limits = SessionTraceLimits(max_events_per_session=2)
    database = tmp_path / "state.db"
    with SqliteSessionTraceStore(database, limits=limits) as store:
        store.create_session("session-1")
        journal = store.journal("session-1")
        events = lifecycle()
        journal.append(events[0])
        journal.append(events[1])

        with pytest.raises(PersistenceError) as captured:
            journal.append(events[2])

        session = store.get_session("session-1")

    assert captured.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    assert session.event_count == 2
    assert len(raw_trace(database)) == 2


def test_event_payload_limit_is_enforced_at_exact_byte_boundary(
    tmp_path: Path,
) -> None:
    event = RunStarted(
        event_id="e" * 96,
        run_id="r" * 96,
        timestamp=datetime.now(UTC) + timedelta(seconds=1),
        max_turns=8,
    )
    canonical = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert len(canonical) > 256

    exact_database = tmp_path / "exact.db"
    exact_limits = SessionTraceLimits(max_event_bytes=len(canonical))
    with SqliteSessionTraceStore(exact_database, limits=exact_limits) as store:
        store.create_session("session-1")
        store.journal("session-1").append(event)
        assert store.get_session("session-1").event_count == 1

    small_database = tmp_path / "small.db"
    small_limits = SessionTraceLimits(max_event_bytes=len(canonical) - 1)
    with SqliteSessionTraceStore(small_database, limits=small_limits) as store:
        store.create_session("session-1")
        with pytest.raises(PersistenceError) as captured:
            store.journal("session-1").append(event)
        assert store.get_session("session-1").event_count == 0

    assert captured.value.code is PersistenceErrorCode.LIMIT_EXCEEDED


def test_trace_insert_failure_rolls_back_projection_and_hides_sql(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        journal = store.journal("session-1")
        events = lifecycle()
        for event in events[:-1]:
            journal.append(event)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER secret_fail_trace
                BEFORE INSERT ON trace_events
                WHEN NEW.event_type = 'run_stopped'
                BEGIN
                    SELECT RAISE(ABORT, 'secret-trigger-error');
                END
                """
            )

        with pytest.raises(PersistenceError) as captured:
            journal.append(events[-1])

        session = store.get_session("session-1")
        run = store.get_run("session-1", "run-1")

    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
    assert "secret-trigger-error" not in captured.value.public_message
    assert session.status is SessionStatus.ACTIVE
    assert session.event_count == 7
    assert run.status is RunStatus.ACTIVE
    assert len(raw_trace(database)) == 7


def test_busy_database_fails_within_configured_budget(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    limits = SessionTraceLimits(busy_timeout_ms=10)
    with SqliteSessionTraceStore(database, limits=limits) as store:
        store.create_session("session-1")
        journal = store.journal("session-1")
        event = lifecycle()[0]
        with (
            closing(sqlite3.connect(database, isolation_level=None)) as blocker,
            blocker,
        ):
            blocker.execute("BEGIN IMMEDIATE")
            with pytest.raises(PersistenceError) as captured:
                journal.append(event)
            blocker.rollback()

    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
