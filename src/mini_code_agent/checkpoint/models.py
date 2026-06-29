from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import TokenUsage

CHECKPOINT_FORMAT_VERSION = 1
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CallId = Annotated[str, Field(min_length=1, max_length=128)]


class CheckpointStatus(StrEnum):
    AVAILABLE = "available"
    CONSUMED = "consumed"


class CheckpointLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_payload_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=1_024,
        le=64 * 1024 * 1024,
    )
    max_messages: int = Field(default=10_000, ge=1, le=100_000)
    max_checkpoints_per_session: int = Field(default=1_000, ge=1, le=100_000)
    max_workspace_files: int = Field(default=20_000, ge=1, le=1_000_000)
    max_workspace_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1_024,
        le=4 * 1024 * 1024 * 1024,
    )


class WorkspaceScanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    excluded_directory_names: frozenset[str] = frozenset(
        {".git", ".venv", ".worktrees", "__pycache__", "node_modules"}
    )

    @field_validator("excluded_directory_names")
    @classmethod
    def validate_excluded_names(cls, names: frozenset[str]) -> frozenset[str]:
        if any(
            not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name
            for name in names
        ):
            raise ValueError("workspace exclusions must be directory names")
        return names


class ResumePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allow_model_retry: bool = False
    allow_read_only_retry: bool = False


class ResumeCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_sha256: str = Field(pattern=_SHA256_PATTERN)


class CheckpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    created_at: datetime
    system_prompt: str
    messages: tuple[Message, ...] = Field(min_length=1, max_length=100_000)
    turns: int = Field(ge=0, le=100)
    tool_calls: int = Field(ge=0, le=1_000)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    seen_call_ids: frozenset[_CallId] = Field(default_factory=frozenset, max_length=1_000)
    tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("checkpoint timestamp must be timezone-aware")
        _validate_state(self.messages, self.turns, self.tool_calls, self.seen_call_ids)
        return self


class CheckpointSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = CHECKPOINT_FORMAT_VERSION
    checkpoint_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    session_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    trace_sequence: int = Field(ge=1, le=1_000_000)
    trace_head_sha256: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime
    system_prompt: str
    messages: tuple[Message, ...] = Field(min_length=1, max_length=100_000)
    turns: int = Field(ge=0, le=100)
    tool_calls: int = Field(ge=0, le=1_000)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    seen_call_ids: frozenset[_CallId] = Field(default_factory=frozenset, max_length=1_000)
    tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_sha256: str = Field(pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: CheckpointStatus = CheckpointStatus.AVAILABLE
    resumed_run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    consumed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("checkpoint timestamp must be timezone-aware")
        if self.status is CheckpointStatus.AVAILABLE:
            if self.resumed_run_id is not None or self.consumed_at is not None:
                raise ValueError("available checkpoint cannot have consumption metadata")
        elif self.resumed_run_id is None or self.consumed_at is None:
            raise ValueError("consumed checkpoint requires consumption metadata")
        if self.consumed_at is not None:
            if self.consumed_at.tzinfo is None or self.consumed_at.utcoffset() is None:
                raise ValueError("checkpoint consumption timestamp must be timezone-aware")
            if self.consumed_at < self.created_at:
                raise ValueError("checkpoint timestamps are inconsistent")
        _validate_state(self.messages, self.turns, self.tool_calls, self.seen_call_ids)
        return self


class ResumePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint: CheckpointSnapshot
    analyzed_event_count: int = Field(ge=1, le=1_000_000)
    analyzed_trace_head_sha256: str = Field(pattern=_SHA256_PATTERN)
    requires_model_retry: bool = False
    requires_read_only_retry: bool = False


class ResumeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint: CheckpointSnapshot
    resumed_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)


def _validate_state(
    messages: tuple[Message, ...],
    turns: int,
    tool_calls: int,
    seen_call_ids: frozenset[str],
) -> None:
    actual_turns, call_ids = _validate_stable_messages(messages)
    if turns != actual_turns:
        raise ValueError("checkpoint turn count is inconsistent")
    if tool_calls != len(call_ids) or seen_call_ids != frozenset(call_ids):
        raise ValueError("checkpoint ToolCall state is inconsistent")


def _validate_stable_messages(messages: tuple[Message, ...]) -> tuple[int, tuple[str, ...]]:
    goal = messages[0]
    if goal.role is not MessageRole.USER or goal.tool_results:
        raise ValueError("checkpoint must start with a user goal")
    turns = 0
    call_ids: list[str] = []
    index = 1
    while index < len(messages):
        assistant = messages[index]
        if assistant.role is not MessageRole.ASSISTANT or not assistant.tool_calls:
            raise ValueError("checkpoint transcript is not at a stable boundary")
        if index + 1 >= len(messages):
            raise ValueError("checkpoint transcript has pending ToolCalls")
        result_message = messages[index + 1]
        expected = tuple(call.id for call in assistant.tool_calls)
        actual = tuple(result.tool_call_id for result in result_message.tool_results)
        if (
            result_message.role is not MessageRole.USER
            or len(result_message.content) != len(result_message.tool_results)
            or actual != expected
            or len(set(expected)) != len(expected)
        ):
            raise ValueError("checkpoint ToolResults do not match ToolCalls")
        if set(call_ids).intersection(expected):
            raise ValueError("checkpoint repeats a ToolCall identifier")
        call_ids.extend(expected)
        turns += 1
        index += 2
    return turns, tuple(call_ids)
