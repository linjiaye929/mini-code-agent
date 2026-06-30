from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.agent.models import StopReason
from mini_code_agent.repair.models import RepairStopReason
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestExecutionStatus,
    PytestReportStatus,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _event_id() -> str:
    return str(uuid4())


class RepairEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=_event_id, pattern=_IDENTIFIER_PATTERN)
    repair_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("repair event timestamp must be timezone-aware")
        return self


class RepairStarted(RepairEventBase):
    type: Literal["repair_started"] = "repair_started"
    scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    max_attempts: int = Field(ge=1, le=10)
    test_target_count: int = Field(ge=0, le=32)
    editable_path_count: int = Field(ge=1, le=32)


class RepairAttemptStarted(RepairEventBase):
    type: Literal["repair_attempt_started"] = "repair_attempt_started"
    attempt: int = Field(ge=1, le=10)
    failure_sha256: str = Field(pattern=_SHA256_PATTERN)


class RepairVerificationStarted(RepairEventBase):
    type: Literal["repair_verification_started"] = "repair_verification_started"
    attempt: int = Field(ge=1, le=10)
    patch_sha256: str = Field(pattern=_SHA256_PATTERN)
    patch_bytes: int = Field(ge=1, le=8 * 1024 * 1024)


class RepairAttemptCompleted(RepairEventBase):
    type: Literal["repair_attempt_completed"] = "repair_attempt_completed"
    attempt: int = Field(ge=1, le=10)
    worker_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    worker_stop_reason: StopReason
    patch_sha256: str = Field(pattern=_SHA256_PATTERN)
    patch_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    test_status: PytestExecutionStatus
    report_status: PytestReportStatus
    counts: PytestCounts
    failure_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    elapsed_ms: int = Field(ge=0, le=3_700_000)

    @model_validator(mode="after")
    def validate_failure_fingerprint(self) -> Self:
        failed = (
            self.test_status is PytestExecutionStatus.FAILED
            and self.report_status is PytestReportStatus.COMPLETE
            and self.counts.failed + self.counts.errors > 0
        )
        if failed != (self.failure_sha256 is not None):
            raise ValueError("failure fingerprint is inconsistent")
        return self


class RepairStopped(RepairEventBase):
    type: Literal["repair_stopped"] = "repair_stopped"
    reason: RepairStopReason
    attempts: int = Field(ge=0, le=10)
    final_status_sha256: str = Field(pattern=_SHA256_PATTERN)
    final_diff_sha256: str = Field(pattern=_SHA256_PATTERN)
    error: str | None = Field(default=None, max_length=500)


RepairEvent = (
    RepairStarted
    | RepairAttemptStarted
    | RepairVerificationStarted
    | RepairAttemptCompleted
    | RepairStopped
)


class RepairJournal(Protocol):
    def append(self, event: RepairEvent) -> None: ...


class NullRepairJournal:
    def append(self, event: RepairEvent) -> None:
        del event


class RecordingRepairJournal:
    def __init__(self) -> None:
        self.events: list[RepairEvent] = []
        self._events_by_id: dict[str, RepairEvent] = {}

    def append(self, event: RepairEvent) -> None:
        existing = self._events_by_id.get(event.event_id)
        if existing is not None:
            if existing == event:
                return
            raise ValueError("repair event identifier conflicts with stored data")
        self._events_by_id[event.event_id] = event
        self.events.append(event)
