from __future__ import annotations

import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PLUGIN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MAX_RESULT_CHARS = 8 * 1024 * 1024

type ProfileTarget = Annotated[str, Field(min_length=1, max_length=1024)]
type PluginName = Annotated[str, Field(min_length=1, max_length=255)]


class PytestExecutionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"
    USAGE_ERROR = "usage_error"
    NO_TESTS = "no_tests"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    UNKNOWN_EXIT = "unknown_exit"


class PytestReportStatus(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    INVALID = "invalid"
    UNSAFE = "unsafe"
    TOO_LARGE = "too_large"


class PytestDiagnosticOutcome(StrEnum):
    FAILURE = "failure"
    ERROR = "error"


class PytestLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_bytes: int = Field(default=1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_report_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_cases: int = Field(default=10_000, ge=1, le=100_000)
    max_diagnostics: int = Field(default=100, ge=1, le=1000)
    max_targets: int = Field(default=32, ge=1, le=64)
    max_message_chars: int = Field(default=4096, ge=1, le=4096)
    max_details_chars: int = Field(default=16_384, ge=1, le=65_536)


class PytestProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    python_executable: Path = Field(default_factory=lambda: Path(sys.executable))
    default_targets: tuple[ProfileTarget, ...] = Field(default=(), max_length=64)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_failures: int = Field(default=20, ge=1, le=1000)
    trusted_plugins: tuple[PluginName, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        executable = str(self.python_executable)
        if (
            not self.python_executable.is_absolute()
            or not executable
            or len(executable) > 4096
            or "\0" in executable
        ):
            raise ValueError("python_executable must be an absolute path")
        if any(not _PLUGIN_PATTERN.fullmatch(plugin) for plugin in self.trusted_plugins):
            raise ValueError("trusted plugin name is invalid")
        if len(set(self.trusted_plugins)) != len(self.trusted_plugins):
            raise ValueError("trusted plugin names must be unique")
        if len(set(self.default_targets)) != len(self.default_targets):
            raise ValueError("default targets must be unique")
        return self

    def validate_against(self, limits: PytestLimits) -> Self:
        if self.timeout_seconds > limits.max_timeout_seconds:
            raise ValueError("profile timeout exceeds configured limits")
        if len(self.default_targets) > limits.max_targets:
            raise ValueError("profile targets exceed configured limits")
        return self


class PytestCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0, le=100_000)
    passed: int = Field(ge=0, le=100_000)
    failed: int = Field(ge=0, le=100_000)
    errors: int = Field(ge=0, le=100_000)
    skipped: int = Field(ge=0, le=100_000)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total != self.passed + self.failed + self.errors + self.skipped:
            raise ValueError("total must equal all outcome counts")
        return self

    @classmethod
    def empty(cls) -> PytestCounts:
        return cls(total=0, passed=0, failed=0, errors=0, skipped=0)


class PytestDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: PytestDiagnosticOutcome
    test_name: str = Field(min_length=1, max_length=1024)
    class_name: str | None = Field(default=None, min_length=1, max_length=1024)
    file: str | None = Field(default=None, min_length=1, max_length=4096)
    line: int | None = Field(default=None, ge=0, le=2_147_483_647)
    message: str = Field(max_length=4096)
    details: str = Field(max_length=16_384)


class PytestRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PytestExecutionStatus
    report_status: PytestReportStatus
    exit_code: int | None
    duration_ms: int = Field(ge=0, le=3_700_000)
    stdout: str = Field(max_length=_MAX_RESULT_CHARS)
    stderr: str = Field(max_length=_MAX_RESULT_CHARS)
    timed_out: bool
    output_limit_exceeded: bool
    counts: PytestCounts
    diagnostics: tuple[PytestDiagnostic, ...] = Field(default=(), max_length=1000)
    diagnostics_truncated: bool = False

    @model_validator(mode="after")
    def validate_report_consistency(self) -> Self:
        if self.report_status is not PytestReportStatus.COMPLETE:
            if self.counts.total or self.diagnostics or self.diagnostics_truncated:
                raise ValueError("incomplete reports cannot contain diagnostics")
            return self

        failures = sum(item.outcome is PytestDiagnosticOutcome.FAILURE for item in self.diagnostics)
        errors = sum(item.outcome is PytestDiagnosticOutcome.ERROR for item in self.diagnostics)
        if failures > self.counts.failed or errors > self.counts.errors:
            raise ValueError("diagnostics exceed report outcome counts")
        expected = self.counts.failed + self.counts.errors
        if not self.diagnostics_truncated and len(self.diagnostics) != expected:
            raise ValueError("diagnostics must cover every failed or errored test")
        if self.diagnostics_truncated and len(self.diagnostics) >= expected:
            raise ValueError("truncated diagnostics must omit at least one item")
        return self


class ParsedPytestReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: PytestCounts
    diagnostics: tuple[PytestDiagnostic, ...] = Field(default=(), max_length=1000)
    diagnostics_truncated: bool = False
