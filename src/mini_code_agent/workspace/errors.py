from __future__ import annotations

from enum import StrEnum


class WorkspaceErrorCode(StrEnum):
    INVALID_PATH = "invalid_path"
    OUTSIDE_WORKSPACE = "outside_workspace"
    LINK_TRAVERSAL = "link_traversal"
    NOT_FOUND = "not_found"
    WRONG_FILE_TYPE = "wrong_file_type"
    TOO_LARGE = "too_large"
    BINARY_FILE = "binary_file"
    INVALID_ENCODING = "invalid_encoding"
    TRAVERSAL_BUDGET = "traversal_budget"
    CONFLICT = "conflict"
    WRITE_FAILED = "write_failed"


class WorkspaceError(RuntimeError):
    def __init__(
        self,
        code: WorkspaceErrorCode,
        public_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable
