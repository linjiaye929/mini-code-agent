from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.providers.base import TokenUsage

_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
_PROFILE_ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_TOOL_NAME = r"^[a-z][a-z0-9_]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"

ToolName = Annotated[str, Field(pattern=_TOOL_NAME)]


class SubagentStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    BATCH_TIMED_OUT = "batch_timed_out"


class SubagentErrorCode(StrEnum):
    INVALID_BATCH = "invalid_batch"
    COMPOSITION_FAILED = "composition_failed"
    CHILD_TIMEOUT = "child_timeout"
    CHILD_FAILED = "child_failed"
    BATCH_TIMEOUT = "batch_timeout"
    RESULT_TOO_LARGE = "result_too_large"


class SubagentError(RuntimeError):
    def __init__(self, code: SubagentErrorCode, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class SubagentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tasks: int = Field(default=4, ge=1, le=4)
    max_concurrency: int = Field(default=2, ge=1, le=4)
    max_task_chars: int = Field(default=4_000, ge=1, le=20_000)
    child_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    batch_timeout_seconds: float = Field(default=300.0, gt=0, le=900)
    max_summary_chars: int = Field(default=8_000, ge=1, le=32_000)
    max_evidence_items: int = Field(default=64, ge=0, le=256)
    max_result_bytes: int = Field(default=131_072, ge=1, le=1_048_576)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.max_concurrency > self.max_tasks:
            raise ValueError("Subagent concurrency cannot exceed task count.")
        if self.batch_timeout_seconds < self.child_timeout_seconds:
            raise ValueError("Batch timeout cannot be lower than child timeout.")
        return self


class SubagentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=_PROFILE_ID)
    local_name: str = Field(pattern=_TOOL_NAME)
    description: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=20_000)
    tool_names: tuple[ToolName, ...] = Field(min_length=1, max_length=16)
    mode: Literal["analysis"] = "analysis"
    agent_limits: AgentLimits = Field(default_factory=AgentLimits)
    limits: SubagentLimits = Field(default_factory=SubagentLimits)

    @field_validator("description", "system_prompt")
    @classmethod
    def reject_nul_text(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("Subagent profile text cannot contain NUL.")
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("Subagent Tool names must be unique.")
        if self.local_name in self.tool_names or any(
            name.startswith("delegate_") for name in self.tool_names
        ):
            raise ValueError("Subagent profiles cannot recurse.")
        if self.agent_limits.max_turns > 32 or self.agent_limits.max_tool_calls > 128:
            raise ValueError("Subagent Agent limits exceed the hard ceiling.")
        if self.agent_limits.max_tool_calls > self.limits.max_evidence_items:
            raise ValueError("Every child ToolCall requires an evidence slot.")
        return self


class SubagentEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=_TOOL_NAME)
    is_error: bool
    content_chars: int = Field(ge=1, le=16_777_216)
    content_sha256: str = Field(pattern=_SHA256)


class SubagentChildResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    child_id: str = Field(pattern=_IDENTIFIER)
    ordinal: int = Field(ge=0, le=3)
    profile_id: str = Field(pattern=_PROFILE_ID)
    status: SubagentStatus
    stop_reason: StopReason | None = None
    turns: int = Field(ge=0, le=32)
    tool_calls: int = Field(ge=0, le=128)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    untrusted_summary: str | None = Field(default=None, max_length=32_000)
    evidence: tuple[SubagentEvidenceItem, ...] = Field(default=(), max_length=256)
    error_code: SubagentErrorCode | None = None
    error_message: str | None = Field(default=None, min_length=1, max_length=500)
    result_sha256: str = Field(pattern=_SHA256)

    @field_validator("untrusted_summary")
    @classmethod
    def reject_nul_summary(cls, value: str | None) -> str | None:
        if value is not None and "\0" in value:
            raise ValueError("Subagent summary cannot contain NUL.")
        return value

    @model_validator(mode="after")
    def validate_status_projection(self) -> Self:
        has_error = self.error_code is not None or self.error_message is not None
        if (self.error_code is None) != (self.error_message is None):
            raise ValueError("Subagent error code and message must be paired.")
        if self.status is SubagentStatus.COMPLETED:
            if self.stop_reason is not StopReason.COMPLETED or has_error:
                raise ValueError("Completed Subagent result is inconsistent.")
        elif self.status is SubagentStatus.STOPPED:
            if self.stop_reason is None or self.stop_reason is StopReason.COMPLETED or has_error:
                raise ValueError("Stopped Subagent result is inconsistent.")
        else:
            expected = {
                SubagentStatus.TIMED_OUT: SubagentErrorCode.CHILD_TIMEOUT,
                SubagentStatus.FAILED: SubagentErrorCode.CHILD_FAILED,
                SubagentStatus.BATCH_TIMED_OUT: SubagentErrorCode.BATCH_TIMEOUT,
            }[self.status]
            if (
                self.stop_reason is not None
                or self.error_code is not expected
                or self.error_message is None
                or self.turns != 0
                or self.tool_calls != 0
                or self.untrusted_summary is not None
                or self.evidence
            ):
                raise ValueError("Failed Subagent result is inconsistent.")
        return self


class SubagentBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=_PROFILE_ID)
    children: tuple[SubagentChildResult, ...] = Field(min_length=1, max_length=4)
    duration_ms: int = Field(ge=0, le=3_700_000)
    completed: int = Field(ge=0, le=4)
    stopped: int = Field(ge=0, le=4)
    timed_out: int = Field(ge=0, le=4)
    failed: int = Field(ge=0, le=4)
    result_sha256: str = Field(pattern=_SHA256)

    @classmethod
    def from_children(
        cls,
        *,
        profile_id: str,
        children: tuple[SubagentChildResult, ...],
        duration_ms: int,
    ) -> Self:
        counts = _status_counts(children)
        projection: dict[str, object] = {
            "profile_id": profile_id,
            "children": [child.model_dump(mode="json") for child in children],
            "duration_ms": duration_ms,
            **counts,
        }
        return cls(
            profile_id=profile_id,
            children=children,
            duration_ms=duration_ms,
            completed=counts["completed"],
            stopped=counts["stopped"],
            timed_out=counts["timed_out"],
            failed=counts["failed"],
            result_sha256=_canonical_sha256(projection),
        )

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if tuple(child.ordinal for child in self.children) != tuple(range(len(self.children))):
            raise ValueError("Subagent child ordinals must be contiguous.")
        if len({child.child_id for child in self.children}) != len(self.children):
            raise ValueError("Subagent child identifiers must be unique.")
        if any(child.profile_id != self.profile_id for child in self.children):
            raise ValueError("Subagent child profile does not match the batch.")
        counts = _status_counts(self.children)
        if any(getattr(self, key) != value for key, value in counts.items()):
            raise ValueError("Subagent batch counts are inconsistent.")
        projection = {
            "profile_id": self.profile_id,
            "children": [child.model_dump(mode="json") for child in self.children],
            "duration_ms": self.duration_ms,
            **counts,
        }
        if self.result_sha256 != _canonical_sha256(projection):
            raise ValueError("Subagent batch hash is inconsistent.")
        return self


def _status_counts(
    children: tuple[SubagentChildResult, ...],
) -> dict[str, int]:
    return {
        "completed": sum(child.status is SubagentStatus.COMPLETED for child in children),
        "stopped": sum(child.status is SubagentStatus.STOPPED for child in children),
        "timed_out": sum(
            child.status in {SubagentStatus.TIMED_OUT, SubagentStatus.BATCH_TIMED_OUT}
            for child in children
        ),
        "failed": sum(child.status is SubagentStatus.FAILED for child in children),
    }


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
