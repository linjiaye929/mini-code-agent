from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mini_code_agent.agent.events import ToolStarted
from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import RunStatus, SessionStatus
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import SessionMode, TrustSource
from mini_code_agent.providers.base import FinishReason, ModelResponse, TokenUsage
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.runtime_info import RuntimeInfoTool
from mini_code_agent.tools.write_file import WriteFileTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary


def runtime_tool_response() -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=(
                ToolCall(
                    id="runtime-1",
                    name="runtime_info",
                    arguments={},
                ),
            ),
        ),
        finish_reason=FinishReason.TOOL_CALL,
        usage=TokenUsage(input_tokens=10, output_tokens=3),
    )


def final_response() -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text("done"),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=20, output_tokens=5),
    )


@pytest.mark.asyncio
async def test_agent_trace_survives_reopen_with_exact_projection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    provider = ScriptedProvider([runtime_tool_response(), final_response()])
    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        result = await AgentRuntime(
            provider,
            RuntimeInfoTool(),
            journal=store.journal("session-1"),
        ).run(
            user_prompt="secret-user-prompt",
            system_prompt="secret-system-prompt",
            run_id="persistent-run",
        )

    with SqliteSessionTraceStore(database) as reopened:
        session = reopened.get_session("session-1")
        run = reopened.get_run("session-1", "persistent-run")
        records = reopened.read_trace("session-1", limit=8)
        verification = reopened.verify_trace("session-1")

    assert result.stop_reason is StopReason.COMPLETED
    assert len(result.messages) == 4
    assert len(provider.requests) == 2
    assert session.status is SessionStatus.COMPLETED
    assert session.event_count == 8
    assert run.status is RunStatus.COMPLETED
    assert run.turns == 2
    assert run.tool_calls == 1
    assert run.input_tokens == 30
    assert run.output_tokens == 8
    assert tuple(record.event.type for record in records) == (
        "run_started",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "model_started",
        "model_completed",
        "run_stopped",
    )
    assert verification.event_count == session.event_count
    assert verification.trace_head_sha256 == session.trace_head_sha256
    persisted = database.read_bytes()
    assert b"secret-user-prompt" not in persisted
    assert b"secret-system-prompt" not in persisted
    assert b"package_version" not in persisted


@pytest.mark.asyncio
async def test_durable_tool_started_prevents_second_governed_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    approval = StaticApprovalHandler(approved=True)
    executor = GovernedToolExecutor(
        ToolRegistry([WriteFileTool(WorkspaceBoundary(workspace))]),
        policy=PolicyEngine(),
        approval=approval,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="write-1",
                            name="write_file",
                            arguments={
                                "path": "first.txt",
                                "content": "first\n",
                                "reason": "Create the first file.",
                            },
                        ),
                        ToolCall(
                            id="write-2",
                            name="write_file",
                            arguments={
                                "path": "second.txt",
                                "content": "second\n",
                                "reason": "Create the second file.",
                            },
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            )
        ]
    )
    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER fail_second_tool_started
                BEFORE INSERT ON trace_events
                WHEN NEW.event_type = 'tool_started'
                  AND NEW.payload_json LIKE '%"tool_call_id":"write-2"%'
                BEGIN
                    SELECT RAISE(ABORT, 'second-tool-journal-failure');
                END
                """
            )

        result = await AgentRuntime(
            provider,
            executor,
            journal=store.journal("session-1"),
        ).run(
            user_prompt="Create two files.",
            run_id="governed-persistent-run",
        )
        records = store.read_trace("session-1", limit=10)
        verification = store.verify_trace("session-1")
        session = store.get_session("session-1")

    assert result.stop_reason is StopReason.PERSISTENCE_ERROR
    assert (workspace / "first.txt").read_text(encoding="utf-8") == "first\n"
    assert not (workspace / "second.txt").exists()
    assert len(approval.requests) == 1
    assert tuple(record.event.type for record in records) == (
        "run_started",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
    )
    started = records[3].event
    assert isinstance(started, ToolStarted)
    assert started.tool_call_id == "write-1"
    assert started.side_effect is SideEffect.WRITE
    assert session.status is SessionStatus.ACTIVE
    assert verification.event_count == 5


@pytest.mark.asyncio
async def test_copied_agent_trace_detects_payload_tampering(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        await AgentRuntime(
            ScriptedProvider([final_response()]),
            RuntimeInfoTool(),
            journal=store.journal("session-1"),
        ).run(user_prompt="inspect", run_id="tamper-run")

    tampered = tmp_path / "tampered.db"
    shutil.copy2(database, tampered)
    with closing(sqlite3.connect(tampered)) as connection, connection:
        connection.execute(
            "UPDATE trace_events SET payload_json = ? WHERE sequence = 2",
            ('{"type":"secret-tampered"}',),
        )

    with (
        SqliteSessionTraceStore(tampered) as reopened,
        pytest.raises(PersistenceError) as captured,
    ):
        reopened.verify_trace("session-1")

    assert captured.value.code is PersistenceErrorCode.TRACE_CORRUPT
    assert "secret-tampered" not in captured.value.public_message
