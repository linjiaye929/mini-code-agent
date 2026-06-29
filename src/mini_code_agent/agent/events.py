from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.agent.models import StopReason
from mini_code_agent.providers.base import FinishReason, TokenUsage
from mini_code_agent.tools.base import SideEffect

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"


def _event_id() -> str:
    return str(uuid4())


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=_event_id, pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunStarted(EventBase):
    type: Literal["run_started"] = "run_started"
    max_turns: int = Field(ge=1, le=100)


class ModelStarted(EventBase):
    type: Literal["model_started"] = "model_started"
    turn: int = Field(ge=1, le=100)
    request_id: str = Field(min_length=1, max_length=193)


class ModelCompleted(EventBase):
    type: Literal["model_completed"] = "model_completed"
    turn: int = Field(ge=1, le=100)
    finish_reason: FinishReason
    usage: TokenUsage


class ToolStarted(EventBase):
    type: Literal["tool_started"] = "tool_started"
    turn: int = Field(ge=1, le=100)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    side_effect: SideEffect


class ToolCompleted(EventBase):
    type: Literal["tool_completed"] = "tool_completed"
    turn: int = Field(ge=1, le=100)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    is_error: bool


class ContextCompacted(EventBase):
    type: Literal["context_compacted"] = "context_compacted"
    turn: int = Field(ge=1)
    estimated_before: int = Field(ge=0, le=2_000_000_000)
    estimated_after: int = Field(ge=0, le=2_000_000_000)
    omitted_messages: int = Field(ge=1)
    omitted_tool_exchanges: int = Field(ge=0)
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_compaction_metadata(self) -> Self:
        if (
            self.estimated_after > self.estimated_before
            or self.omitted_tool_exchanges * 2 > self.omitted_messages
        ):
            raise ValueError("context compaction metadata is inconsistent")
        return self


class CheckpointSaved(EventBase):
    type: Literal["checkpoint_saved"] = "checkpoint_saved"
    checkpoint_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    turn: int = Field(ge=0, le=100)
    message_count: int = Field(ge=1, le=100_000)
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunStopped(EventBase):
    type: Literal["run_stopped"] = "run_stopped"
    turns: int = Field(ge=0, le=100)
    reason: StopReason
    tool_calls: int = Field(default=0, ge=0, le=1000)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str | None = Field(default=None, max_length=500)


AgentEvent = (
    RunStarted
    | ModelStarted
    | ModelCompleted
    | ToolStarted
    | ToolCompleted
    | ContextCompacted
    | CheckpointSaved
    | RunStopped
)


class EventJournal(Protocol):
    def append(self, event: AgentEvent) -> None: ...


class EventSink(Protocol):
    def publish(self, event: AgentEvent) -> None: ...


class NullEventSink:
    def publish(self, event: AgentEvent) -> None:
        del event


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def publish(self, event: AgentEvent) -> None:
        self.events.append(event)
