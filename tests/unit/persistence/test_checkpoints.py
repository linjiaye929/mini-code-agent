from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mini_code_agent.agent.events import CheckpointSaved, RunStarted
from mini_code_agent.checkpoint.codec import transcript_sha256
from mini_code_agent.checkpoint.models import CheckpointDraft, CheckpointLimits
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.providers.base import TokenUsage


def draft(**overrides: object) -> CheckpointDraft:
    values: dict[str, object] = {
        "checkpoint_id": "checkpoint-1",
        "source_run_id": "run-1",
        "created_at": datetime.now(UTC) + timedelta(seconds=1),
        "system_prompt": "be precise",
        "messages": (
            Message.user_text("inspect"),
            Message(
                role=MessageRole.ASSISTANT,
                content=(ToolCall(id="call-1", name="read_file", arguments={}),),
            ),
            Message(
                role=MessageRole.USER,
                content=(ToolResult(tool_call_id="call-1", content="ok"),),
            ),
        ),
        "turns": 1,
        "tool_calls": 1,
        "usage": TokenUsage(input_tokens=10, output_tokens=4),
        "seen_call_ids": frozenset({"call-1"}),
        "tool_contract_sha256": "a" * 64,
        "workspace_sha256": "b" * 64,
    }
    values.update(overrides)
    return CheckpointDraft.model_validate(values)


def active_store(
    database: Path,
    *,
    checkpoint_limits: CheckpointLimits | None = None,
) -> SqliteSessionTraceStore:
    store = SqliteSessionTraceStore(database, checkpoint_limits=checkpoint_limits)
    store.initialize()
    store.create_session("session-1")
    store.journal("session-1").append(
        RunStarted(
            run_id="run-1",
            timestamp=datetime.now(UTC),
            max_turns=8,
        )
    )
    return store


def test_checkpoint_save_binds_trace_and_round_trips(tmp_path: Path) -> None:
    store = active_store(tmp_path / "state.db")

    saved = store.checkpoints("session-1").save(draft())
    loaded = store.get_checkpoint("session-1", "checkpoint-1")
    trace = store.read_trace("session-1", limit=10)

    assert loaded == saved
    assert saved.trace_sequence == 2
    assert saved.trace_head_sha256 == trace[-1].event_sha256
    assert isinstance(trace[-1].event, CheckpointSaved)
    assert trace[-1].event.checkpoint_id == saved.checkpoint_id
    assert trace[-1].event.transcript_sha256 == transcript_sha256(saved.messages)
    assert store.latest_checkpoint("session-1") == saved
    assert store.list_checkpoints("session-1", limit=1) == (saved,)


def test_checkpoint_exact_retry_is_idempotent_but_conflict_fails(
    tmp_path: Path,
) -> None:
    store = active_store(tmp_path / "state.db")
    journal = store.checkpoints("session-1")
    original = draft()

    first = journal.save(original)
    second = journal.save(original)
    with pytest.raises(PersistenceError) as captured:
        journal.save(draft(system_prompt="changed"))

    assert second == first
    assert store.get_session("session-1").event_count == 2
    assert captured.value.code is PersistenceErrorCode.CHECKPOINT_CONFLICT


def test_checkpoint_save_rolls_back_trace_when_row_insert_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = active_store(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER fail_checkpoint BEFORE INSERT ON checkpoints
            BEGIN SELECT RAISE(ABORT, 'secret checkpoint failure'); END
            """
        )

    with pytest.raises(PersistenceError) as captured:
        store.checkpoints("session-1").save(draft())

    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
    assert store.get_session("session-1").event_count == 1
    assert len(store.read_trace("session-1", limit=10)) == 1


def test_checkpoint_limits_and_corruption_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "secret-state.db"
    store = active_store(
        database,
        checkpoint_limits=CheckpointLimits(max_messages=1),
    )
    with pytest.raises(PersistenceError) as oversized:
        store.checkpoints("session-1").save(draft())
    assert oversized.value.code is PersistenceErrorCode.LIMIT_EXCEEDED

    normal = SqliteSessionTraceStore(database)
    normal.initialize()
    saved = normal.checkpoints("session-1").save(draft())
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE checkpoints SET payload_json = ? WHERE checkpoint_id = ?",
            ('{"secret":"tampered"}', saved.checkpoint_id),
        )
    with pytest.raises(PersistenceError) as corrupt:
        normal.get_checkpoint("session-1", saved.checkpoint_id)
    assert corrupt.value.code is PersistenceErrorCode.TRACE_CORRUPT
    assert "secret" not in corrupt.value.public_message
    assert str(database) not in corrupt.value.public_message
