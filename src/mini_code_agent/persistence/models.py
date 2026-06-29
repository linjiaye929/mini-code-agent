from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from mini_code_agent.agent.events import AgentEvent
from mini_code_agent.agent.models import StopReason

IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
EMPTY_TRACE_SHA256 = "0" * 64
SCHEMA_VERSION = 1


class SessionStatus(StrEnum):
    READY = "ready"
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"


class RunStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"


class SessionTraceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_event_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    max_events_per_session: int = Field(default=100_000, ge=1, le=1_000_000)
    max_query_rows: int = Field(default=1_000, ge=1, le=10_000)
    busy_timeout_ms: int = Field(default=250, ge=1, le=5_000)


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(pattern=IDENTIFIER_PATTERN)
    schema_version: Literal[1] = SCHEMA_VERSION
    created_at: datetime
    updated_at: datetime
    status: SessionStatus
    last_run_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    event_count: int = Field(ge=0, le=1_000_000)
    next_sequence: int = Field(ge=1, le=1_000_001)
    trace_head_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_timestamps_and_counters(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("session timestamps are inconsistent")
        if self.next_sequence != self.event_count + 1:
            raise ValueError("session counters are inconsistent")
        if self.event_count == 0 and self.trace_head_sha256 != EMPTY_TRACE_SHA256:
            raise ValueError("empty session trace head is inconsistent")
        return self


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    session_id: str = Field(pattern=IDENTIFIER_PATTERN)
    started_at: datetime
    stopped_at: datetime | None = None
    status: RunStatus
    stop_reason: StopReason | None = None
    turns: int = Field(default=0, ge=0, le=100)
    tool_calls: int = Field(default=0, ge=0, le=1_000)
    input_tokens: int = Field(default=0, ge=0, le=2_000_000_000)
    output_tokens: int = Field(default=0, ge=0, le=2_000_000_000)

    @model_validator(mode="after")
    def validate_status_metadata(self) -> Self:
        if self.stopped_at is not None and self.stopped_at < self.started_at:
            raise ValueError("run timestamps are inconsistent")
        if self.status is RunStatus.ACTIVE:
            if self.stopped_at is not None or self.stop_reason is not None:
                raise ValueError("active run cannot contain terminal metadata")
            return self
        if self.stopped_at is None or self.stop_reason is None:
            raise ValueError("terminal run requires stop metadata")
        if (self.status is RunStatus.COMPLETED) != (self.stop_reason is StopReason.COMPLETED):
            raise ValueError("run status and stop reason are inconsistent")
        return self


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    sequence: int = Field(ge=1, le=1_000_000)
    session_id: str = Field(pattern=IDENTIFIER_PATTERN)
    event: AgentEvent
    previous_sha256: str = Field(pattern=SHA256_PATTERN)
    event_sha256: str = Field(pattern=SHA256_PATTERN)

    @computed_field
    @property
    def run_id(self) -> str:
        return self.event.run_id

    @computed_field
    @property
    def event_id(self) -> str:
        return self.event.event_id


class TraceVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(pattern=IDENTIFIER_PATTERN)
    event_count: int = Field(ge=0, le=1_000_000)
    trace_head_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_empty_head(self) -> Self:
        if self.event_count == 0 and self.trace_head_sha256 != EMPTY_TRACE_SHA256:
            raise ValueError("empty trace verification head is inconsistent")
        return self
