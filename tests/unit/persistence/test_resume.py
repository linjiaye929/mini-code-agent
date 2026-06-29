from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mini_code_agent.agent.events import (
    ModelCompleted,
    ModelStarted,
    RunStarted,
    ToolCompleted,
    ToolStarted,
)
from mini_code_agent.agent.models import StopReason
from mini_code_agent.checkpoint.models import (
    CheckpointDraft,
    ResumeCompatibility,
    ResumePolicy,
)
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import RunStatus
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.providers.base import FinishReason, TokenUsage
from mini_code_agent.tools.base import SideEffect


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


def active_store(database: Path) -> SqliteSessionTraceStore:
    store = SqliteSessionTraceStore(database)
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


def compatibility(
    *,
    tool: str = "a" * 64,
    workspace: str = "b" * 64,
) -> ResumeCompatibility:
    return ResumeCompatibility(
        tool_contract_sha256=tool,
        workspace_sha256=workspace,
    )


def test_resume_analysis_accepts_compatible_checkpoint_without_mutation(
    tmp_path: Path,
) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())
    before = store.get_session("session-1")

    plan = store.analyze_resume(
        "session-1",
        saved.checkpoint_id,
        compatibility=compatibility(),
    )

    assert plan.checkpoint == saved
    assert plan.analyzed_event_count == before.event_count
    assert plan.analyzed_trace_head_sha256 == before.trace_head_sha256
    assert plan.requires_model_retry is False
    assert store.get_session("session-1") == before


def test_resume_analysis_requires_explicit_model_retry(tmp_path: Path) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())
    store.journal("session-1").append(
        ModelStarted(
            run_id="run-1",
            timestamp=saved.created_at + timedelta(milliseconds=1),
            turn=2,
            request_id="run-1:2",
        )
    )

    with pytest.raises(PersistenceError) as captured:
        store.analyze_resume(
            "session-1",
            saved.checkpoint_id,
            compatibility=compatibility(),
        )
    plan = store.analyze_resume(
        "session-1",
        saved.checkpoint_id,
        compatibility=compatibility(),
        policy=ResumePolicy(allow_model_retry=True),
    )

    assert captured.value.code is PersistenceErrorCode.REPLAY_REQUIRES_APPROVAL
    assert plan.requires_model_retry is True


def test_resume_analysis_requires_read_only_retry_policy(tmp_path: Path) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())
    timestamp = saved.created_at
    journal = store.journal("session-1")
    journal.append(
        ModelStarted(
            run_id="run-1",
            timestamp=timestamp + timedelta(milliseconds=1),
            turn=2,
            request_id="run-1:2",
        )
    )
    journal.append(
        ModelCompleted(
            run_id="run-1",
            timestamp=timestamp + timedelta(milliseconds=2),
            turn=2,
            finish_reason=FinishReason.TOOL_CALL,
            usage=TokenUsage(input_tokens=1),
        )
    )
    journal.append(
        ToolStarted(
            run_id="run-1",
            timestamp=timestamp + timedelta(milliseconds=3),
            turn=2,
            tool_call_id="read-2",
            tool_name="read_file",
            side_effect=SideEffect.READ_ONLY,
        )
    )
    journal.append(
        ToolCompleted(
            run_id="run-1",
            timestamp=timestamp + timedelta(milliseconds=4),
            turn=2,
            tool_call_id="read-2",
            tool_name="read_file",
            is_error=False,
        )
    )

    with pytest.raises(PersistenceError) as captured:
        store.analyze_resume(
            "session-1",
            saved.checkpoint_id,
            compatibility=compatibility(),
            policy=ResumePolicy(allow_model_retry=True),
        )
    plan = store.analyze_resume(
        "session-1",
        saved.checkpoint_id,
        compatibility=compatibility(),
        policy=ResumePolicy(
            allow_model_retry=True,
            allow_read_only_retry=True,
        ),
    )

    assert captured.value.code is PersistenceErrorCode.REPLAY_REQUIRES_APPROVAL
    assert plan.requires_read_only_retry is True


@pytest.mark.parametrize(
    "side_effect",
    [SideEffect.WRITE, SideEffect.EXECUTE, SideEffect.NETWORK],
)
def test_resume_analysis_blocks_any_uncheckpointed_side_effect(
    tmp_path: Path,
    side_effect: SideEffect,
) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())
    store.journal("session-1").append(
        ToolStarted(
            run_id="run-1",
            timestamp=saved.created_at + timedelta(milliseconds=1),
            turn=2,
            tool_call_id="risk-2",
            tool_name="dangerous_tool",
            side_effect=side_effect,
        )
    )

    with pytest.raises(PersistenceError) as captured:
        store.analyze_resume(
            "session-1",
            saved.checkpoint_id,
            compatibility=compatibility(),
            policy=ResumePolicy(
                allow_model_retry=True,
                allow_read_only_retry=True,
            ),
        )

    assert captured.value.code is PersistenceErrorCode.INDETERMINATE_SIDE_EFFECT


@pytest.mark.parametrize(
    "current",
    [
        compatibility(tool="c" * 64),
        compatibility(workspace="d" * 64),
    ],
)
def test_resume_analysis_rejects_compatibility_drift(
    tmp_path: Path,
    current: ResumeCompatibility,
) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())

    with pytest.raises(PersistenceError) as captured:
        store.analyze_resume(
            "session-1",
            saved.checkpoint_id,
            compatibility=current,
        )

    assert captured.value.code is PersistenceErrorCode.RESUME_INCOMPATIBLE


def test_resume_analysis_rejects_checkpoint_older_than_latest(tmp_path: Path) -> None:
    store = active_store(tmp_path / "state.db")
    first = store.checkpoints("session-1").save(draft())
    store.checkpoints("session-1").save(
        draft(
            checkpoint_id="checkpoint-2",
            created_at=first.created_at + timedelta(milliseconds=1),
        )
    )

    with pytest.raises(PersistenceError) as captured:
        store.analyze_resume(
            "session-1",
            first.checkpoint_id,
            compatibility=compatibility(),
        )

    assert captured.value.code is PersistenceErrorCode.CHECKPOINT_STALE


def test_resume_claim_atomically_interrupts_source_and_starts_new_run(
    tmp_path: Path,
) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())
    plan = store.analyze_resume(
        "session-1",
        saved.checkpoint_id,
        compatibility=compatibility(),
    )

    state = store.claim_resume(plan, resumed_run_id="run-2", max_turns=8)

    source = store.get_run("session-1", "run-1")
    resumed = store.get_run("session-1", "run-2")
    checkpoint = store.get_checkpoint("session-1", saved.checkpoint_id)
    assert state.checkpoint == checkpoint
    assert checkpoint.status.value == "consumed"
    assert checkpoint.resumed_run_id == "run-2"
    assert source.status is RunStatus.STOPPED
    assert source.stop_reason is StopReason.INTERRUPTED
    assert resumed.status is RunStatus.ACTIVE
    assert store.get_session("session-1").last_run_id == "run-2"
    assert store.get_session("session-1").event_count == plan.analyzed_event_count + 2


def test_resume_claim_rejects_stale_plan_without_mutation(tmp_path: Path) -> None:
    store = active_store(tmp_path / "state.db")
    saved = store.checkpoints("session-1").save(draft())
    plan = store.analyze_resume(
        "session-1",
        saved.checkpoint_id,
        compatibility=compatibility(),
    )
    store.journal("session-1").append(
        ModelStarted(
            run_id="run-1",
            timestamp=saved.created_at + timedelta(milliseconds=1),
            turn=2,
            request_id="run-1:2",
        )
    )
    before = store.get_session("session-1")

    with pytest.raises(PersistenceError) as captured:
        store.claim_resume(plan, resumed_run_id="run-2", max_turns=8)

    assert captured.value.code is PersistenceErrorCode.CHECKPOINT_STALE
    assert store.get_session("session-1") == before
    assert store.get_run("session-1", "run-1").status is RunStatus.ACTIVE


def test_resume_claim_rolls_back_both_events_when_consumption_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = active_store(database)
    saved = store.checkpoints("session-1").save(draft())
    plan = store.analyze_resume(
        "session-1",
        saved.checkpoint_id,
        compatibility=compatibility(),
    )
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER fail_consumption BEFORE UPDATE OF status ON checkpoints
            BEGIN SELECT RAISE(ABORT, 'secret claim failure'); END
            """
        )

    with pytest.raises(PersistenceError) as captured:
        store.claim_resume(plan, resumed_run_id="run-2", max_turns=8)

    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
    assert store.get_session("session-1").event_count == plan.analyzed_event_count
    assert store.get_run("session-1", "run-1").status is RunStatus.ACTIVE
    with pytest.raises(PersistenceError) as missing:
        store.get_run("session-1", "run-2")
    assert missing.value.code is PersistenceErrorCode.RUN_NOT_FOUND
