from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.subagents.models import SubagentStatus

_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
_PROFILE_ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"


def _event_id() -> str:
    return str(uuid4())


class SubagentEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=_event_id, pattern=_IDENTIFIER)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    parent_tool_call_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(pattern=_PROFILE_ID)


class SubagentBatchStarted(SubagentEventBase):
    type: Literal["subagent_batch_started"] = "subagent_batch_started"
    task_count: int = Field(ge=1, le=4)


class SubagentStarted(SubagentEventBase):
    type: Literal["subagent_started"] = "subagent_started"
    child_id: str = Field(pattern=_IDENTIFIER)
    ordinal: int = Field(ge=0, le=3)


class SubagentCompleted(SubagentEventBase):
    type: Literal["subagent_completed"] = "subagent_completed"
    child_id: str = Field(pattern=_IDENTIFIER)
    ordinal: int = Field(ge=0, le=3)
    status: SubagentStatus
    duration_ms: int = Field(ge=0, le=3_700_000)
    turns: int = Field(ge=0, le=32)
    tool_calls: int = Field(ge=0, le=128)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    result_sha256: str = Field(pattern=_SHA256)


class SubagentBatchCompleted(SubagentEventBase):
    type: Literal["subagent_batch_completed"] = "subagent_batch_completed"
    duration_ms: int = Field(ge=0, le=3_700_000)
    completed: int = Field(ge=0, le=4)
    stopped: int = Field(ge=0, le=4)
    timed_out: int = Field(ge=0, le=4)
    failed: int = Field(ge=0, le=4)
    result_sha256: str = Field(pattern=_SHA256)


SubagentEvent = SubagentBatchStarted | SubagentStarted | SubagentCompleted | SubagentBatchCompleted


class SubagentEventSink(Protocol):
    def publish(self, event: SubagentEvent) -> None: ...


class NullSubagentEventSink:
    def publish(self, event: SubagentEvent) -> None:
        del event


class RecordingSubagentEventSink:
    def __init__(self) -> None:
        self.events: list[SubagentEvent] = []

    def publish(self, event: SubagentEvent) -> None:
        self.events.append(event)
