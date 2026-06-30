from __future__ import annotations

import os
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

from mini_code_agent.testing.errors import PytestReportError, PytestReportErrorCode
from mini_code_agent.testing.models import (
    ParsedPytestReport,
    PytestCounts,
    PytestDiagnostic,
    PytestDiagnosticOutcome,
    PytestLimits,
)

_TRUNCATION_MARKER = "...[truncated]"


def parse_junit_report(
    path: Path,
    limits: PytestLimits,
) -> ParsedPytestReport:
    content = _read_report(path, limits.max_report_bytes)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise _report_error(PytestReportErrorCode.INVALID) from None

    folded = text.casefold()
    if "<!doctype" in folded or "<!entity" in folded:
        raise _report_error(PytestReportErrorCode.UNSAFE)

    try:
        # Input is byte-bounded, strict UTF-8, and rejects DTD/entity declarations above.
        root = ET.fromstring(text)  # nosec B314
    except ET.ParseError:
        raise _report_error(PytestReportErrorCode.INVALID) from None
    if root.tag not in {"testsuite", "testsuites"}:
        raise _report_error(PytestReportErrorCode.INVALID)

    total = passed = failed = errors = skipped = 0
    diagnostics: list[PytestDiagnostic] = []
    for case in root.iter("testcase"):
        total += 1
        if total > limits.max_cases:
            raise _report_error(PytestReportErrorCode.TOO_LARGE)

        name = case.attrib.get("name")
        if name is None or not name.strip():
            raise _report_error(PytestReportErrorCode.INVALID)
        line = _parse_line(case.attrib.get("line"))

        outcomes = [child for child in case if child.tag in {"failure", "error", "skipped"}]
        if len(outcomes) > 1:
            raise _report_error(PytestReportErrorCode.INVALID)
        if not outcomes:
            passed += 1
            continue

        outcome = outcomes[0]
        if outcome.tag == "skipped":
            skipped += 1
            continue

        diagnostic_outcome: PytestDiagnosticOutcome
        if outcome.tag == "failure":
            failed += 1
            diagnostic_outcome = PytestDiagnosticOutcome.FAILURE
        else:
            errors += 1
            diagnostic_outcome = PytestDiagnosticOutcome.ERROR

        if len(diagnostics) < limits.max_diagnostics:
            diagnostics.append(
                _build_diagnostic(
                    case,
                    outcome,
                    diagnostic_outcome,
                    line,
                    limits,
                )
            )

    counts = PytestCounts(
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
    )
    return ParsedPytestReport(
        counts=counts,
        diagnostics=tuple(diagnostics),
        diagnostics_truncated=failed + errors > len(diagnostics),
    )


def _read_report(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise _report_error(PytestReportErrorCode.MISSING) from None
    except OSError:
        raise _report_error(PytestReportErrorCode.UNSAFE) from None

    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise _report_error(PytestReportErrorCode.UNSAFE)
            content = stream.read(limit + 1)
    except PytestReportError:
        raise
    except OSError:
        raise _report_error(PytestReportErrorCode.INVALID) from None
    if len(content) > limit:
        raise _report_error(PytestReportErrorCode.TOO_LARGE)
    return content


def _build_diagnostic(
    case: ET.Element,
    outcome: ET.Element,
    diagnostic_outcome: PytestDiagnosticOutcome,
    line: int | None,
    limits: PytestLimits,
) -> PytestDiagnostic:
    class_name = _optional_bounded(case.attrib.get("classname"), 1024)
    file_name = _optional_bounded(case.attrib.get("file"), 4096)
    return PytestDiagnostic(
        outcome=diagnostic_outcome,
        test_name=_truncate(case.attrib["name"], 1024),
        class_name=class_name,
        file=file_name,
        line=line,
        message=_truncate(
            outcome.attrib.get("message", ""),
            limits.max_message_chars,
        ),
        details=_truncate(
            "".join(outcome.itertext()),
            limits.max_details_chars,
        ),
    )


def _parse_line(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        line = int(value)
    except ValueError:
        raise _report_error(PytestReportErrorCode.INVALID) from None
    if line < 0 or line > 2_147_483_647:
        raise _report_error(PytestReportErrorCode.INVALID)
    return line


def _optional_bounded(value: str | None, limit: int) -> str | None:
    if value is None or not value:
        return None
    return _truncate(value, limit)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= len(_TRUNCATION_MARKER):
        return value[:limit]
    return f"{value[: limit - len(_TRUNCATION_MARKER)]}{_TRUNCATION_MARKER}"


def _report_error(code: PytestReportErrorCode) -> PytestReportError:
    messages = {
        PytestReportErrorCode.MISSING: "Pytest report was not found.",
        PytestReportErrorCode.INVALID: "Pytest report was invalid.",
        PytestReportErrorCode.UNSAFE: "Pytest report was not safe to read.",
        PytestReportErrorCode.TOO_LARGE: "Pytest report exceeded its configured limit.",
    }
    return PytestReportError(code, messages[code])
