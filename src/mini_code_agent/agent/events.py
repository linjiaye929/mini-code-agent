from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.agent.models import StopReason
from mini_code_agent.providers.base import FinishReason, TokenUsage


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunStarted(EventBase):
    type: Literal["run_started"] = "run_started"
    max_turns: int


class ModelCompleted(EventBase):
    type: Literal["model_completed"] = "model_completed"
    turn: int
    finish_reason: FinishReason
    usage: TokenUsage


class ToolCompleted(EventBase):
    type: Literal["tool_completed"] = "tool_completed"
    turn: int
    tool_call_id: str
    tool_name: str
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


class RunStopped(EventBase):
    type: Literal["run_stopped"] = "run_stopped"
    turns: int
    reason: StopReason
    error: str | None = None


AgentEvent = RunStarted | ModelCompleted | ToolCompleted | ContextCompacted | RunStopped


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
