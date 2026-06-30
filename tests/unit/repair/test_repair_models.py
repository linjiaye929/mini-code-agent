from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from mini_code_agent.agent.models import StopReason
from mini_code_agent.repair.models import (
    RepairAttemptRecord,
    RepairLimits,
    RepairRequest,
    RepairResult,
    RepairStopReason,
    RepairTestSummary,
    RepairWorkerRequest,
)
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestDiagnostic,
    PytestDiagnosticOutcome,
    PytestExecutionStatus,
    PytestReportStatus,
)

SHA = "a" * 64


def test_repair_limits_defaults_and_hard_caps() -> None:
    limits = RepairLimits()

    assert limits.max_attempts == 3
    assert limits.max_elapsed_seconds == 900
    assert limits.max_patch_bytes == 256 * 1024
    assert limits.max_same_failure == 2
    assert limits.max_prompt_chars == 64 * 1024

    invalid_values = (
        {"max_attempts": 11},
        {"max_elapsed_seconds": 3_601},
        {"max_patch_bytes": 8 * 1024 * 1024 + 1},
        {"max_same_failure": 6},
        {"max_prompt_chars": 256 * 1024 + 1},
    )
    for values in invalid_values:
        with pytest.raises(ValidationError):
            RepairLimits.model_validate(values)


def test_repair_request_requires_unique_bounded_paths_and_targets() -> None:
    request = RepairRequest(
        repair_id="repair-1",
        user_prompt="Fix addition.",
        system_prompt="Keep the patch focused.",
        test_targets=("tests",),
        editable_paths=("src/calculator.py",),
        reason="Repair the failing arithmetic test.",
    )

    assert request.repair_id == "repair-1"
    assert request.editable_paths == ("src/calculator.py",)

    with pytest.raises(ValidationError, match="editable_paths must be unique"):
        RepairRequest(
            user_prompt="Fix.",
            test_targets=("tests",),
            editable_paths=("src/a.py", "src/a.py"),
            reason="Verify.",
        )
    with pytest.raises(ValidationError, match="test_targets must be unique"):
        RepairRequest(
            user_prompt="Fix.",
            test_targets=("tests", "tests"),
            editable_paths=("src/a.py",),
            reason="Verify.",
        )


@pytest.mark.parametrize(
    "repair_id",
    ("", "-leading", "contains space", "x" * 97),
)
def test_repair_request_rejects_invalid_identifier(repair_id: str) -> None:
    with pytest.raises(ValidationError):
        RepairRequest(
            repair_id=repair_id,
            user_prompt="Fix.",
            test_targets=("tests",),
            editable_paths=("src/a.py",),
            reason="Verify.",
        )


def test_worker_request_rejects_inconsistent_remaining_budget() -> None:
    with pytest.raises(ValidationError, match="remaining attempt budget is inconsistent"):
        RepairWorkerRequest(
            repair_id="repair-1",
            attempt=2,
            max_attempts=2,
            remaining_attempts=2,
            user_prompt="Fix.",
            editable_paths=("src/a.py",),
            last_test=failed_summary(),
            remaining_elapsed_ms=10_000,
            remaining_patch_bytes=1024,
        )


def test_attempt_record_requires_matching_failure_fingerprint() -> None:
    with pytest.raises(ValidationError, match="failure fingerprint is inconsistent"):
        attempt_record(test=failed_summary(), failure_sha256=None)

    passed = passed_summary()
    record = attempt_record(test=passed, failure_sha256=None)
    assert record.test.status is PytestExecutionStatus.PASSED


def test_test_summary_requires_failure_diagnostics() -> None:
    with pytest.raises(ValidationError, match="diagnostics are inconsistent"):
        RepairTestSummary(
            status=PytestExecutionStatus.FAILED,
            report_status=PytestReportStatus.COMPLETE,
            counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
            failure_sha256=SHA,
        )


def test_result_succeeds_only_for_trusted_terminal_reasons() -> None:
    assert repair_result(RepairStopReason.REPAIRED).succeeded is True
    assert repair_result(RepairStopReason.ALREADY_PASSING).succeeded is True
    assert repair_result(RepairStopReason.MAX_ATTEMPTS).succeeded is False


def test_result_requires_ordered_attempts_and_matching_final_test() -> None:
    first = attempt_record(attempt=1)
    second = attempt_record(attempt=2)

    result = repair_result(
        RepairStopReason.MAX_ATTEMPTS,
        attempts=(first, second),
        final_test=second.test,
    )
    assert len(result.attempts) == 2

    with pytest.raises(ValidationError, match="attempt sequence is inconsistent"):
        repair_result(
            RepairStopReason.MAX_ATTEMPTS,
            attempts=(second, first),
            final_test=first.test,
        )
    with pytest.raises(ValidationError, match="final test does not match"):
        repair_result(
            RepairStopReason.MAX_ATTEMPTS,
            attempts=(first,),
            final_test=passed_summary(),
        )


def failed_summary() -> RepairTestSummary:
    return RepairTestSummary(
        status=PytestExecutionStatus.FAILED,
        report_status=PytestReportStatus.COMPLETE,
        counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
        diagnostics=(
            PytestDiagnostic(
                outcome=PytestDiagnosticOutcome.FAILURE,
                test_name="test_add",
                file="tests/test_calculator.py",
                line=4,
                message="assert -1 == 3",
                details="left = -1, right = 3",
            ),
        ),
        failure_sha256=SHA,
    )


def passed_summary() -> RepairTestSummary:
    return RepairTestSummary(
        status=PytestExecutionStatus.PASSED,
        report_status=PytestReportStatus.COMPLETE,
        counts=PytestCounts(total=1, passed=1, failed=0, errors=0, skipped=0),
    )


def attempt_record(**overrides: Any) -> RepairAttemptRecord:
    values: dict[str, object] = {
        "attempt": 1,
        "worker_run_id": "repair-1-attempt-1",
        "worker_stop_reason": StopReason.COMPLETED,
        "patch_sha256": "b" * 64,
        "patch_bytes": 10,
        "test": failed_summary(),
        "failure_sha256": SHA,
        "elapsed_ms": 50,
    }
    values.update(overrides)
    return RepairAttemptRecord.model_validate(values)


def repair_result(
    reason: RepairStopReason,
    **overrides: Any,
) -> RepairResult:
    values: dict[str, object] = {
        "repair_id": "repair-1",
        "stop_reason": reason,
        "editable_paths": ("src/a.py",),
        "scope_sha256": "c" * 64,
        "attempts": (),
        "final_status_sha256": "d" * 64,
        "final_diff_sha256": "e" * 64,
    }
    values.update(overrides)
    return RepairResult.model_validate(values)
