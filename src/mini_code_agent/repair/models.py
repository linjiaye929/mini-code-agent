from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.agent.models import StopReason
from mini_code_agent.testing.models import (
    ProfileTarget,
    PytestCounts,
    PytestExecutionStatus,
    PytestReportStatus,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _repair_id() -> str:
    return str(uuid4())


class RepairStopReason(StrEnum):
    ALREADY_PASSING = "already_passing"
    REPAIRED = "repaired"
    NOT_APPROVED = "not_approved"
    INVALID_SCOPE = "invalid_scope"
    DIRTY_REPOSITORY = "dirty_repository"
    TEST_INFRASTRUCTURE_ERROR = "test_infrastructure_error"
    TEST_MUTATED_REPOSITORY = "test_mutated_repository"
    WORKER_FAILED = "worker_failed"
    NO_PROGRESS = "no_progress"
    SCOPE_VIOLATION = "scope_violation"
    PATCH_LIMIT = "patch_limit"
    REPEATED_FAILURE = "repeated_failure"
    MAX_ATTEMPTS = "max_attempts"
    TIME_LIMIT = "time_limit"
    PERSISTENCE_ERROR = "persistence_error"


class RepairLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10)
    max_elapsed_seconds: float = Field(default=900, gt=0, le=3600)
    max_patch_bytes: int = Field(default=256 * 1024, ge=1, le=8 * 1024 * 1024)
    max_same_failure: int = Field(default=2, ge=1, le=5)
    max_prompt_chars: int = Field(default=64 * 1024, ge=1024, le=256 * 1024)


class RepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repair_id: str = Field(default_factory=_repair_id, pattern=_IDENTIFIER_PATTERN)
    user_prompt: str = Field(min_length=1, max_length=32_768)
    system_prompt: str = Field(default="", max_length=32_768)
    test_targets: tuple[ProfileTarget, ...] = Field(default=(), max_length=32)
    editable_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unique_values(self) -> Self:
        if len(set(self.test_targets)) != len(self.test_targets):
            raise ValueError("test_targets must be unique")
        if len(set(self.editable_paths)) != len(self.editable_paths):
            raise ValueError("editable_paths must be unique")
        return self


class RepairPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repair_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    test_targets: tuple[ProfileTarget, ...] = Field(default=(), max_length=32)
    editable_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    max_attempts: int = Field(ge=1, le=10)
    max_elapsed_seconds: float = Field(gt=0, le=3600)
    max_patch_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    reason: str = Field(min_length=1, max_length=500)


class RepairTestSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PytestExecutionStatus
    report_status: PytestReportStatus
    counts: PytestCounts
    failure_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_failure_fingerprint(self) -> Self:
        is_repairable_failure = (
            self.status is PytestExecutionStatus.FAILED
            and self.report_status is PytestReportStatus.COMPLETE
            and self.counts.failed + self.counts.errors > 0
        )
        if is_repairable_failure != (self.failure_sha256 is not None):
            raise ValueError("test failure fingerprint is inconsistent")
        return self


class RepairWorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repair_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    attempt: int = Field(ge=1, le=10)
    max_attempts: int = Field(ge=1, le=10)
    remaining_attempts: int = Field(ge=1, le=10)
    user_prompt: str = Field(min_length=1, max_length=32_768)
    system_prompt: str = Field(default="", max_length=32_768)
    editable_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    last_test: RepairTestSummary
    remaining_elapsed_ms: int = Field(ge=0, le=3_600_000)
    remaining_patch_bytes: int = Field(ge=0, le=8 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_attempt_budget(self) -> Self:
        expected = self.max_attempts - self.attempt + 1
        if self.attempt > self.max_attempts or self.remaining_attempts != expected:
            raise ValueError("remaining attempt budget is inconsistent")
        return self


class RepairAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int = Field(ge=1, le=10)
    worker_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    worker_stop_reason: StopReason
    patch_sha256: str = Field(pattern=_SHA256_PATTERN)
    patch_bytes: int = Field(ge=0, le=8 * 1024 * 1024)
    test: RepairTestSummary
    failure_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    elapsed_ms: int = Field(ge=0, le=3_700_000)

    @model_validator(mode="after")
    def validate_failure_fingerprint(self) -> Self:
        if self.failure_sha256 != self.test.failure_sha256:
            raise ValueError("failure fingerprint is inconsistent")
        return self


class RepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repair_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    stop_reason: RepairStopReason
    editable_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_test: RepairTestSummary | None = None
    final_test: RepairTestSummary | None = None
    attempts: tuple[RepairAttemptRecord, ...] = Field(default=(), max_length=10)
    final_status_sha256: str = Field(pattern=_SHA256_PATTERN)
    final_diff_sha256: str = Field(pattern=_SHA256_PATTERN)
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_attempts(self) -> Self:
        if tuple(item.attempt for item in self.attempts) != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("attempt sequence is inconsistent")
        if self.attempts and self.final_test != self.attempts[-1].test:
            raise ValueError("final test does not match the last attempt")
        return self

    @property
    def succeeded(self) -> bool:
        return self.stop_reason in {
            RepairStopReason.ALREADY_PASSING,
            RepairStopReason.REPAIRED,
        }
