from enum import StrEnum


class PytestReportErrorCode(StrEnum):
    MISSING = "missing"
    INVALID = "invalid"
    UNSAFE = "unsafe"
    TOO_LARGE = "too_large"


class PytestReportError(Exception):
    def __init__(
        self,
        code: PytestReportErrorCode,
        public_message: str,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
