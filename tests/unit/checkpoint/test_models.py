from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mini_code_agent.checkpoint.models import (
    CheckpointLimits,
    CheckpointSnapshot,
    CheckpointStatus,
    ResumeCompatibility,
    ResumePolicy,
)
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import TokenUsage


def stable_messages() -> tuple[Message, ...]:
    return (
        Message.user_text("inspect"),
        Message(
            role=MessageRole.ASSISTANT,
            content=(ToolCall(id="call-1", name="read_file", arguments={"path": "a.py"}),),
        ),
        Message(
            role=MessageRole.USER,
            content=(ToolResult(tool_call_id="call-1", content="content"),),
        ),
    )


def snapshot(**overrides: object) -> CheckpointSnapshot:
    values: dict[str, object] = {
        "checkpoint_id": "checkpoint-1",
        "session_id": "session-1",
        "source_run_id": "run-1",
        "trace_sequence": 2,
        "trace_head_sha256": "a" * 64,
        "created_at": datetime.now(UTC),
        "system_prompt": "be precise",
        "messages": stable_messages(),
        "turns": 1,
        "tool_calls": 1,
        "usage": TokenUsage(input_tokens=10, output_tokens=4),
        "seen_call_ids": frozenset({"call-1"}),
        "tool_contract_sha256": "b" * 64,
        "workspace_sha256": "c" * 64,
        "payload_sha256": "d" * 64,
    }
    values.update(overrides)
    return CheckpointSnapshot.model_validate(values)


def test_checkpoint_limits_and_resume_defaults_are_bounded() -> None:
    limits = CheckpointLimits()
    policy = ResumePolicy()

    assert limits.max_payload_bytes == 4 * 1024 * 1024
    assert limits.max_messages == 10_000
    assert limits.max_checkpoints_per_session == 1_000
    assert limits.max_workspace_files == 20_000
    assert limits.max_workspace_bytes == 64 * 1024 * 1024
    assert policy.allow_model_retry is False
    assert policy.allow_read_only_retry is False

    with pytest.raises(ValidationError):
        CheckpointLimits(max_payload_bytes=1_023)
    with pytest.raises(ValidationError):
        CheckpointLimits(max_workspace_files=0)


def test_checkpoint_snapshot_is_immutable_and_consistent() -> None:
    saved = snapshot()

    assert saved.format_version == 1
    assert saved.status is CheckpointStatus.AVAILABLE
    assert saved.seen_call_ids == frozenset({"call-1"})
    with pytest.raises(ValidationError):
        saved.turns = 2


_INVALID_SNAPSHOTS: list[dict[str, object]] = [
    {"messages": (Message.user_text("inspect"),), "turns": 1},
    {"tool_calls": 2},
    {"seen_call_ids": frozenset()},
    {
        "messages": (
            Message.user_text("inspect"),
            Message(
                role=MessageRole.ASSISTANT,
                content=(ToolCall(id="call-1", name="read_file", arguments={}),),
            ),
        )
    },
    {
        "messages": (
            Message.user_text("inspect"),
            Message(
                role=MessageRole.ASSISTANT,
                content=(ToolCall(id="call-1", name="read_file", arguments={}),),
            ),
            Message(
                role=MessageRole.USER,
                content=(ToolResult(tool_call_id="other", content="content"),),
            ),
        )
    },
    {
        "messages": (
            Message(
                role=MessageRole.USER,
                content=(ToolResult(tool_call_id="call-1", content="content"),),
            ),
        ),
        "turns": 0,
        "tool_calls": 0,
        "seen_call_ids": frozenset(),
    },
]


@pytest.mark.parametrize("overrides", _INVALID_SNAPSHOTS)
def test_checkpoint_snapshot_rejects_unstable_or_forged_state(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        snapshot(**overrides)


def test_resume_compatibility_requires_hashes() -> None:
    compatibility = ResumeCompatibility(
        tool_contract_sha256="a" * 64,
        workspace_sha256="b" * 64,
    )

    assert compatibility.tool_contract_sha256 == "a" * 64
    with pytest.raises(ValidationError):
        ResumeCompatibility(
            tool_contract_sha256="not-a-hash",
            workspace_sha256="b" * 64,
        )
