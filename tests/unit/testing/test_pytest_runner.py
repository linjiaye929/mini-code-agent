from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.command.models import CommandRequest, CommandResult
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestExecutionStatus,
    PytestLimits,
    PytestProfile,
    PytestReportStatus,
)
from mini_code_agent.testing.pytest_runner import PytestRunner

EMPTY_REPORT = b'<testsuite name="empty" tests="0" />'


class RecordingCommandRunner:
    def __init__(
        self,
        *,
        exit_code: int | None = 0,
        timed_out: bool = False,
        output_limit_exceeded: bool = False,
        report: bytes | None = EMPTY_REPORT,
        error: CommandError | None = None,
        cancel: bool = False,
        echo_report_path: bool = False,
    ) -> None:
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.output_limit_exceeded = output_limit_exceeded
        self.report = report
        self.error = error
        self.cancel = cancel
        self.echo_report_path = echo_report_path
        self.requests: list[CommandRequest] = []
        self.report_paths: list[Path] = []

    async def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        report_path = Path(
            next(
                argument.removeprefix("--junitxml=")
                for argument in request.argv
                if argument.startswith("--junitxml=")
            )
        )
        self.report_paths.append(report_path)
        if self.cancel:
            raise asyncio.CancelledError
        if self.error is not None:
            raise self.error
        if self.echo_report_path:
            report_path.write_text(
                f"""<testsuite>
  <testcase name="test_path">
    <failure message="{report_path.name}">{report_path}</failure>
  </testcase>
</testsuite>""",
                encoding="utf-8",
            )
        elif self.report is None:
            report_path.unlink(missing_ok=True)
        else:
            report_path.write_bytes(self.report)
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd_display,
            exit_code=self.exit_code,
            stdout=(
                f"captured stdout {report_path}" if self.echo_report_path else "captured stdout"
            ),
            stderr=(
                f"captured stderr {report_path.name}"
                if self.echo_report_path
                else "captured stderr"
            ),
            timed_out=self.timed_out,
            output_limit_exceeded=self.output_limit_exceeded,
            stdout_truncated=self.output_limit_exceeded,
            stderr_truncated=False,
            duration_ms=25,
        )


def profile_for(tmp_path: Path) -> PytestProfile:
    return PytestProfile(
        python_executable=(tmp_path / "host-python").resolve(),
        timeout_seconds=30,
        max_failures=3,
        trusted_plugins=("pytest_asyncio.plugin",),
    )


def test_preview_argv_has_fixed_shape_and_managed_report_marker(
    tmp_path: Path,
) -> None:
    runner = PytestRunner(tmp_path, profile=profile_for(tmp_path))

    argv = runner.preview_argv(("tests/unit", "tests/test_api.py"))

    assert argv == (
        str((tmp_path / "host-python").resolve()),
        "-I",
        "-B",
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        "--maxfail=3",
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_asyncio.plugin",
        "--junitxml=<managed-junit-report.xml>",
        "--",
        "tests/unit",
        "tests/test_api.py",
    )


def test_maximum_profile_and_targets_fit_64_argument_command_contract(
    tmp_path: Path,
) -> None:
    profile = PytestProfile(
        python_executable=(tmp_path / "host-python").resolve(),
        trusted_plugins=tuple(f"plugin_{index}" for index in range(10)),
    )
    runner = PytestRunner(tmp_path, profile=profile)

    argv = runner.preview_argv(tuple(f"tests/test_{index}.py" for index in range(32)))

    assert len(argv) == 64


@pytest.mark.asyncio
async def test_runner_executes_exact_argv_and_cleans_temporary_report(
    tmp_path: Path,
) -> None:
    command_runner = RecordingCommandRunner()
    runner = PytestRunner(
        tmp_path,
        profile=profile_for(tmp_path),
        command_runner=command_runner,
    )

    result = await runner.run(("tests/test_api.py",))

    request = command_runner.requests[0]
    report_path = command_runner.report_paths[0]
    assert request.cwd == tmp_path.resolve()
    assert request.cwd_display == "."
    assert request.timeout_seconds == 30
    assert request.argv[:12] == (
        str((tmp_path / "host-python").resolve()),
        "-I",
        "-B",
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        "--maxfail=3",
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_asyncio.plugin",
    )
    assert request.argv[12].startswith("--junitxml=")
    assert request.argv[13:] == ("--", "tests/test_api.py")
    assert report_path.exists() is False
    assert str(report_path) not in result.model_dump_json()


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "output_exceeded", "expected"),
    [
        (0, False, False, PytestExecutionStatus.PASSED),
        (1, False, False, PytestExecutionStatus.FAILED),
        (2, False, False, PytestExecutionStatus.INTERRUPTED),
        (3, False, False, PytestExecutionStatus.INTERNAL_ERROR),
        (4, False, False, PytestExecutionStatus.USAGE_ERROR),
        (5, False, False, PytestExecutionStatus.NO_TESTS),
        (17, False, False, PytestExecutionStatus.UNKNOWN_EXIT),
        (None, True, False, PytestExecutionStatus.TIMED_OUT),
        (None, False, True, PytestExecutionStatus.OUTPUT_LIMIT_EXCEEDED),
    ],
)
@pytest.mark.asyncio
async def test_runner_classifies_process_outcomes(
    tmp_path: Path,
    exit_code: int | None,
    timed_out: bool,
    output_exceeded: bool,
    expected: PytestExecutionStatus,
) -> None:
    command_runner = RecordingCommandRunner(
        exit_code=exit_code,
        timed_out=timed_out,
        output_limit_exceeded=output_exceeded,
    )
    runner = PytestRunner(
        tmp_path,
        profile=profile_for(tmp_path),
        command_runner=command_runner,
    )

    result = await runner.run(())

    assert result.status is expected
    assert result.report_status is PytestReportStatus.COMPLETE
    assert result.counts == PytestCounts.empty()
    assert result.exit_code == exit_code
    assert result.stdout == "captured stdout"
    assert result.stderr == "captured stderr"


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (None, PytestReportStatus.MISSING),
        (b"<testsuite>", PytestReportStatus.INVALID),
        (b"<!DOCTYPE x><testsuite/>", PytestReportStatus.UNSAFE),
        (b"<testsuite />" + b" " * 64, PytestReportStatus.TOO_LARGE),
    ],
)
@pytest.mark.asyncio
async def test_runner_preserves_process_result_when_report_is_unusable(
    tmp_path: Path,
    report: bytes | None,
    expected: PytestReportStatus,
) -> None:
    command_runner = RecordingCommandRunner(exit_code=1, report=report)
    runner = PytestRunner(
        tmp_path,
        profile=profile_for(tmp_path),
        limits=PytestLimits(max_report_bytes=32),
        command_runner=command_runner,
    )

    result = await runner.run(())

    assert result.status is PytestExecutionStatus.FAILED
    assert result.report_status is expected
    assert result.counts == PytestCounts.empty()
    assert result.diagnostics == ()
    assert result.stdout == "captured stdout"


@pytest.mark.asyncio
async def test_runner_propagates_command_error_after_report_cleanup(
    tmp_path: Path,
) -> None:
    command_runner = RecordingCommandRunner(
        error=CommandError(
            CommandErrorCode.COMMAND_NOT_FOUND,
            "Command executable was not found.",
        )
    )
    runner = PytestRunner(
        tmp_path,
        profile=profile_for(tmp_path),
        command_runner=command_runner,
    )

    with pytest.raises(CommandError) as captured:
        await runner.run(())

    assert captured.value.code is CommandErrorCode.COMMAND_NOT_FOUND
    assert command_runner.report_paths[0].exists() is False


@pytest.mark.asyncio
async def test_runner_propagates_cancellation_after_report_cleanup(
    tmp_path: Path,
) -> None:
    command_runner = RecordingCommandRunner(cancel=True)
    runner = PytestRunner(
        tmp_path,
        profile=profile_for(tmp_path),
        command_runner=command_runner,
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.run(())

    assert command_runner.report_paths[0].exists() is False


@pytest.mark.asyncio
async def test_runner_redacts_managed_report_path_echoes(
    tmp_path: Path,
) -> None:
    command_runner = RecordingCommandRunner(
        exit_code=1,
        echo_report_path=True,
    )
    runner = PytestRunner(
        tmp_path,
        profile=profile_for(tmp_path),
        command_runner=command_runner,
    )

    result = await runner.run(())

    report_path = command_runner.report_paths[0]
    serialized = result.model_dump_json()
    assert str(report_path) not in serialized
    assert report_path.as_posix() not in serialized
    assert report_path.name not in serialized
    assert serialized.count("<managed-junit-report.xml>") >= 4


def test_profile_timeout_must_fit_runner_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        PytestRunner(
            tmp_path,
            profile=PytestProfile(
                python_executable=(tmp_path / "python").resolve(),
                timeout_seconds=31,
            ),
            limits=PytestLimits(max_timeout_seconds=30),
        )


def test_workspace_root_must_be_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace"):
        PytestRunner(tmp_path / "missing", profile=profile_for(tmp_path))
