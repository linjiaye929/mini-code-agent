from __future__ import annotations

from enum import StrEnum


class PersistenceErrorCode(StrEnum):
    DATABASE_UNAVAILABLE = "database_unavailable"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    SESSION_EXISTS = "session_exists"
    SESSION_NOT_FOUND = "session_not_found"
    RUN_NOT_FOUND = "run_not_found"
    INVALID_IDENTIFIER = "invalid_identifier"
    RUN_CONFLICT = "run_conflict"
    INVALID_TRANSITION = "invalid_transition"
    EVENT_CONFLICT = "event_conflict"
    LIMIT_EXCEEDED = "limit_exceeded"
    TRACE_CORRUPT = "trace_corrupt"
    STORAGE_FAILED = "storage_failed"


class PersistenceError(RuntimeError):
    def __init__(
        self,
        code: PersistenceErrorCode,
        public_message: str,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
