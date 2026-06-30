from pathlib import Path

import pytest
from pydantic import ValidationError

from mini_code_agent.testing.models import (
    PytestCounts,
    PytestDiagnostic,
    PytestDiagnosticOutcome,
    PytestExecutionStatus,
    PytestLimits,
    PytestProfile,
    PytestReportStatus,
    PytestRunResult,
)


def test_pytest_limits_are_bounded_and_frozen() -> None:
    limits = PytestLimits(
        max_output_bytes=1024,
        max_timeout_seconds=30,
        max_report_bytes=2048,
        max_cases=10,
        max_diagnostics=2,
        max_targets=4,
        max_message_chars=100,
        max_details_chars=200,
    )

    assert limits.max_report_bytes == 2048
    with pytest.raises(ValidationError):
        limits.max_cases = 11


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_output_bytes", 0),
        ("max_timeout_seconds", 3601),
        ("max_report_bytes", 8 * 1024 * 1024 + 1),
        ("max_cases", 0),
        ("max_diagnostics", 1001),
        ("max_targets", 65),
        ("max_message_chars", 0),
        ("max_details_chars", 65_537),
    ],
)
def test_pytest_limits_reject_values_outside_hard_caps(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        PytestLimits.model_validate({field: value})


def test_pytest_profile_requires_absolute_executable_and_valid_plugins(
    tmp_path: Path,
) -> None:
    executable = (tmp_path / "python").resolve()
    profile = PytestProfile(
        python_executable=executable,
        default_targets=("tests",),
        timeout_seconds=30,
        max_failures=5,
        trusted_plugins=("pytest_asyncio.plugin", "company_pytest"),
    )

    assert profile.python_executable == executable
    assert profile.trusted_plugins == ("pytest_asyncio.plugin", "company_pytest")
    with pytest.raises(ValidationError):
        profile.timeout_seconds = 31


@pytest.mark.parametrize(
    "python_executable",
    [
        Path("python"),
        Path(""),
    ],
)
def test_pytest_profile_rejects_non_absolute_executable(
    python_executable: Path,
) -> None:
    with pytest.raises(ValidationError):
        PytestProfile(python_executable=python_executable)


@pytest.mark.parametrize(
    "plugin",
    [
        "",
        "-p",
        "plugin-name",
        "plugin/name",
        "plugin..module",
        "plugin;command",
    ],
)
def test_pytest_profile_rejects_untrusted_plugin_syntax(
    tmp_path: Path,
    plugin: str,
) -> None:
    with pytest.raises(ValidationError):
        PytestProfile(
            python_executable=(tmp_path / "python").resolve(),
            trusted_plugins=(plugin,),
        )


def test_pytest_profile_timeout_cannot_exceed_limits(tmp_path: Path) -> None:
    profile = PytestProfile(
        python_executable=(tmp_path / "python").resolve(),
        timeout_seconds=31,
    )

    with pytest.raises(ValueError, match="timeout"):
        profile.validate_against(PytestLimits(max_timeout_seconds=30))


def test_pytest_counts_require_components_to_equal_total() -> None:
    counts = PytestCounts(total=4, passed=1, failed=1, errors=1, skipped=1)

    assert counts.total == 4
    with pytest.raises(ValidationError, match="total"):
        PytestCounts(total=5, passed=1, failed=1, errors=1, skipped=1)


def test_pytest_result_contains_typed_bounded_diagnostics() -> None:
    diagnostic = PytestDiagnostic(
        outcome=PytestDiagnosticOutcome.FAILURE,
        test_name="test_total",
        class_name="tests.test_invoice.TestInvoice",
        file="tests/test_invoice.py",
        line=12,
        message="assert 1 == 2",
        details="traceback",
    )
    result = PytestRunResult(
        status=PytestExecutionStatus.FAILED,
        report_status=PytestReportStatus.COMPLETE,
        exit_code=1,
        duration_ms=20,
        stdout="",
        stderr="",
        timed_out=False,
        output_limit_exceeded=False,
        counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
        diagnostics=(diagnostic,),
        diagnostics_truncated=False,
    )

    assert result.diagnostics == (diagnostic,)
    assert result.model_dump(mode="json")["status"] == "failed"


def test_pytest_diagnostic_rejects_fields_above_hard_caps() -> None:
    with pytest.raises(ValidationError):
        PytestDiagnostic(
            outcome=PytestDiagnosticOutcome.ERROR,
            test_name="x" * 1025,
            message="message",
            details="details",
        )
    with pytest.raises(ValidationError):
        PytestDiagnostic(
            outcome=PytestDiagnosticOutcome.ERROR,
            test_name="test_error",
            message="x" * 4097,
            details="details",
        )
    with pytest.raises(ValidationError):
        PytestDiagnostic(
            outcome=PytestDiagnosticOutcome.ERROR,
            test_name="test_error",
            message="message",
            details="x" * 16_385,
        )


def test_complete_report_requires_counts_to_cover_returned_diagnostics() -> None:
    diagnostic = PytestDiagnostic(
        outcome=PytestDiagnosticOutcome.FAILURE,
        test_name="test_failure",
        message="failed",
        details="details",
    )

    with pytest.raises(ValidationError, match="diagnostics"):
        PytestRunResult(
            status=PytestExecutionStatus.PASSED,
            report_status=PytestReportStatus.COMPLETE,
            exit_code=0,
            duration_ms=1,
            stdout="",
            stderr="",
            timed_out=False,
            output_limit_exceeded=False,
            counts=PytestCounts(total=1, passed=1, failed=0, errors=0, skipped=0),
            diagnostics=(diagnostic,),
            diagnostics_truncated=False,
        )
