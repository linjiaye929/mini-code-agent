from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX_ARGUMENT_CHARS = 4096
_MAX_RESULT_CHARS = 8 * 1024 * 1024


class CommandLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_bytes: int = Field(default=1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    cleanup_timeout_seconds: float = Field(default=2.0, gt=0, le=10)


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd: Path
    cwd_display: str = Field(min_length=1, max_length=1024)
    timeout_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def validate_argv(self) -> Self:
        if not self.argv[0] or any(
            len(argument) > _MAX_ARGUMENT_CHARS or "\0" in argument for argument in self.argv
        ):
            raise ValueError("argv is invalid")
        return self


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd: str = Field(min_length=1, max_length=1024)
    exit_code: int | None
    stdout: str = Field(max_length=_MAX_RESULT_CHARS)
    stderr: str = Field(max_length=_MAX_RESULT_CHARS)
    timed_out: bool
    output_limit_exceeded: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int = Field(ge=0, le=3_700_000)
