from __future__ import annotations

from mini_code_agent.repair.fingerprint import failure_sha256, scope_sha256
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestDiagnostic,
    PytestDiagnosticOutcome,
    PytestExecutionStatus,
    PytestReportStatus,
    PytestRunResult,
)


def test_scope_fingerprint_is_canonical_but_path_sensitive() -> None:
    assert scope_sha256(("src/a.py", "src/b.py")) == scope_sha256(("src/b.py", "src/a.py"))
    assert scope_sha256(("src/a.py",)) != scope_sha256(("src/A.py",))


def test_failure_fingerprint_ignores_order_details_output_and_duration() -> None:
    left = failed_result(
        duration_ms=10,
        stdout="left stdout",
        stderr="left stderr",
        diagnostics=(
            diagnostic("test_b", details="first details"),
            diagnostic("test_a", details="stable"),
        ),
    )
    right = failed_result(
        duration_ms=999,
        stdout="right stdout",
        stderr="right stderr",
        diagnostics=(
            diagnostic("test_a", details="changed details"),
            diagnostic("test_b", details="other details"),
        ),
    )

    assert failure_sha256(left) == failure_sha256(right)


def test_failure_fingerprint_changes_for_stable_diagnostic_fields() -> None:
    assert failure_sha256(
        failed_result(diagnostics=(diagnostic("test_a", message="left"),))
    ) != failure_sha256(failed_result(diagnostics=(diagnostic("test_a", message="right"),)))


def test_failure_fingerprint_changes_for_process_or_report_status() -> None:
    failed = failed_result()
    missing = failed.model_copy(
        update={
            "report_status": PytestReportStatus.MISSING,
            "counts": PytestCounts.empty(),
            "diagnostics": (),
        }
    )
    internal = missing.model_copy(update={"status": PytestExecutionStatus.INTERNAL_ERROR})

    assert failure_sha256(failed) != failure_sha256(missing)
    assert failure_sha256(missing) != failure_sha256(internal)


def failed_result(
    *,
    duration_ms: int = 10,
    stdout: str = "",
    stderr: str = "",
    diagnostics: tuple[PytestDiagnostic, ...] | None = None,
) -> PytestRunResult:
    active_diagnostics = diagnostics or (diagnostic("test_failure"),)
    return PytestRunResult(
        status=PytestExecutionStatus.FAILED,
        report_status=PytestReportStatus.COMPLETE,
        exit_code=1,
        duration_ms=duration_ms,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        output_limit_exceeded=False,
        counts=PytestCounts(
            total=len(active_diagnostics),
            passed=0,
            failed=len(active_diagnostics),
            errors=0,
            skipped=0,
        ),
        diagnostics=active_diagnostics,
    )


def diagnostic(
    name: str,
    *,
    message: str = "assert failed",
    details: str = "traceback",
) -> PytestDiagnostic:
    return PytestDiagnostic(
        outcome=PytestDiagnosticOutcome.FAILURE,
        test_name=name,
        class_name="TestMath",
        file="tests/test_math.py",
        line=10,
        message=message,
        details=details,
    )
