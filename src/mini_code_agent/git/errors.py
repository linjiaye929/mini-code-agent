from __future__ import annotations

from enum import StrEnum


class GitErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    NOT_REPOSITORY = "not_repository"
    OUTSIDE_WORKSPACE = "outside_workspace"
    BARE_REPOSITORY = "bare_repository"
    COMMAND_FAILED = "command_failed"
    TIMEOUT = "timeout"
    LIMIT_EXCEEDED = "limit_exceeded"
    INVALID_OUTPUT = "invalid_output"


class GitError(RuntimeError):
    def __init__(self, code: GitErrorCode) -> None:
        message = "Git operation could not be completed."
        super().__init__(message)
        self.code = code
        self.public_message = message
