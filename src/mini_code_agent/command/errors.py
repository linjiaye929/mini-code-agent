from __future__ import annotations

from enum import StrEnum


class CommandErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    COMMAND_NOT_FOUND = "command_not_found"
    COMMAND_START_FAILED = "command_start_failed"
    COMMAND_CLEANUP_FAILED = "command_cleanup_failed"


class CommandError(RuntimeError):
    def __init__(self, code: CommandErrorCode, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
