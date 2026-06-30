from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.command.models import CommandLimits, CommandRequest, CommandResult
from mini_code_agent.command.runner import CommandRunner
from mini_code_agent.testing.errors import PytestReportError, PytestReportErrorCode
from mini_code_agent.testing.junit import parse_junit_report
from mini_code_agent.testing.models import (
    ParsedPytestReport,
    PytestCounts,
    PytestExecutionStatus,
    PytestLimits,
    PytestProfile,
    PytestReportStatus,
    PytestRunResult,
)

_REPORT_MARKER = "<managed-junit-report.xml>"


class PytestCommandRunner(Protocol):
    async def run(self, request: CommandRequest) -> CommandResult: ...


class PytestRunner:
    def __init__(
        self,
        workspace_root: Path,
        *,
        profile: PytestProfile | None = None,
        limits: PytestLimits | None = None,
        command_runner: PytestCommandRunner | None = None,
    ) -> None:
        try:
            root = workspace_root.resolve(strict=True)
        except OSError:
            raise ValueError("workspace root must be an existing directory") from None
        if not root.is_dir():
            raise ValueError("workspace root must be an existing directory")

        self._limits = limits or PytestLimits()
        self._profile = (profile or PytestProfile()).validate_against(self._limits)
        self._workspace_root = root
        self._command_runner = command_runner or CommandRunner(
            limits=CommandLimits(
                max_output_bytes=self._limits.max_output_bytes,
                max_timeout_seconds=self._limits.max_timeout_seconds,
            ),
            environment_overrides={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )

    @property
    def profile(self) -> PytestProfile:
        return self._profile

    @property
    def limits(self) -> PytestLimits:
        return self._limits

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def preview_argv(self, targets: tuple[str, ...]) -> tuple[str, ...]:
        return self._build_argv(_REPORT_MARKER, targets)

    async def run(self, targets: tuple[str, ...]) -> PytestRunResult:
        report_path = self._create_report_path()
        try:
            request = CommandRequest(
                argv=self._build_argv(str(report_path), targets),
                cwd=self._workspace_root,
                cwd_display=".",
                timeout_seconds=self._profile.timeout_seconds,
            )
            command_result = await self._command_runner.run(request)
            parsed, report_status = self._parse_report(report_path)
            parsed = _redact_report_diagnostics(parsed, report_path)
            return PytestRunResult(
                status=_classify(command_result),
                report_status=report_status,
                exit_code=command_result.exit_code,
                duration_ms=command_result.duration_ms,
                stdout=_redact_report_path(command_result.stdout, report_path),
                stderr=_redact_report_path(command_result.stderr, report_path),
                timed_out=command_result.timed_out,
                output_limit_exceeded=command_result.output_limit_exceeded,
                counts=parsed.counts,
                diagnostics=parsed.diagnostics,
                diagnostics_truncated=parsed.diagnostics_truncated,
            )
        finally:
            with suppress(OSError):
                report_path.unlink(missing_ok=True)

    def _build_argv(
        self,
        report_path: str,
        targets: tuple[str, ...],
    ) -> tuple[str, ...]:
        plugin_arguments = ("-p", "no:cacheprovider")
        trusted_plugin_arguments = tuple(
            argument for plugin in self._profile.trusted_plugins for argument in ("-p", plugin)
        )
        return (
            str(self._profile.python_executable),
            "-I",
            "-B",
            "-m",
            "pytest",
            "-q",
            "--disable-warnings",
            f"--maxfail={self._profile.max_failures}",
            *plugin_arguments,
            *trusted_plugin_arguments,
            f"--junitxml={report_path}",
            "--",
            *targets,
        )

    @staticmethod
    def _create_report_path() -> Path:
        try:
            with tempfile.NamedTemporaryFile(
                prefix="mini-code-agent-pytest-",
                suffix=".xml",
                delete=False,
            ) as stream:
                return Path(stream.name)
        except OSError:
            raise CommandError(
                CommandErrorCode.COMMAND_IO_FAILED,
                "Pytest report file could not be created.",
            ) from None

    def _parse_report(
        self,
        report_path: Path,
    ) -> tuple[ParsedPytestReport, PytestReportStatus]:
        try:
            return (
                parse_junit_report(report_path, self._limits),
                PytestReportStatus.COMPLETE,
            )
        except PytestReportError as exc:
            status_by_code = {
                PytestReportErrorCode.MISSING: PytestReportStatus.MISSING,
                PytestReportErrorCode.INVALID: PytestReportStatus.INVALID,
                PytestReportErrorCode.UNSAFE: PytestReportStatus.UNSAFE,
                PytestReportErrorCode.TOO_LARGE: PytestReportStatus.TOO_LARGE,
            }
            return (
                ParsedPytestReport(
                    counts=PytestCounts.empty(),
                    diagnostics=(),
                    diagnostics_truncated=False,
                ),
                status_by_code[exc.code],
            )


def _classify(result: CommandResult) -> PytestExecutionStatus:
    if result.output_limit_exceeded:
        return PytestExecutionStatus.OUTPUT_LIMIT_EXCEEDED
    if result.timed_out:
        return PytestExecutionStatus.TIMED_OUT
    statuses: dict[int, PytestExecutionStatus] = {
        0: PytestExecutionStatus.PASSED,
        1: PytestExecutionStatus.FAILED,
        2: PytestExecutionStatus.INTERRUPTED,
        3: PytestExecutionStatus.INTERNAL_ERROR,
        4: PytestExecutionStatus.USAGE_ERROR,
        5: PytestExecutionStatus.NO_TESTS,
    }
    if result.exit_code is None:
        return PytestExecutionStatus.UNKNOWN_EXIT
    return statuses.get(result.exit_code, PytestExecutionStatus.UNKNOWN_EXIT)


def _redact_report_diagnostics(
    parsed: ParsedPytestReport,
    report_path: Path,
) -> ParsedPytestReport:
    diagnostics = tuple(
        item.model_copy(
            update={
                "test_name": _redact_report_path(item.test_name, report_path),
                "class_name": (
                    _redact_report_path(item.class_name, report_path)
                    if item.class_name is not None
                    else None
                ),
                "file": (
                    _redact_report_path(item.file, report_path) if item.file is not None else None
                ),
                "message": _redact_report_path(item.message, report_path),
                "details": _redact_report_path(item.details, report_path),
            }
        )
        for item in parsed.diagnostics
    )
    return parsed.model_copy(update={"diagnostics": diagnostics})


def _redact_report_path(value: str, report_path: Path) -> str:
    candidates = sorted(
        {str(report_path), report_path.as_posix(), report_path.name},
        key=len,
        reverse=True,
    )
    for candidate in candidates:
        if os.name == "nt":
            value = re.sub(
                re.escape(candidate),
                _REPORT_MARKER,
                value,
                flags=re.IGNORECASE,
            )
        else:
            value = value.replace(candidate, _REPORT_MARKER)
    return value
