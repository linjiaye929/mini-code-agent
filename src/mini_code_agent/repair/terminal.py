from __future__ import annotations

from mini_code_agent.git.models import GitDiffResult, GitStatusSnapshot
from mini_code_agent.repair.events import RepairJournal, RepairStopped
from mini_code_agent.repair.models import (
    RepairAttemptRecord,
    RepairRequest,
    RepairResult,
    RepairStopReason,
    RepairTestSummary,
)
from mini_code_agent.repair.scope import RepairScope


def stop_repair(
    *,
    journal: RepairJournal,
    request: RepairRequest,
    scope: RepairScope,
    reason: RepairStopReason,
    status: GitStatusSnapshot,
    diff: GitDiffResult,
    baseline: RepairTestSummary | None = None,
    final: RepairTestSummary | None = None,
    attempts: tuple[RepairAttemptRecord, ...] = (),
    error: str | None = None,
) -> RepairResult:
    try:
        journal.append(
            RepairStopped(
                repair_id=request.repair_id,
                reason=reason,
                attempts=len(attempts),
                final_status_sha256=status.sha256,
                final_diff_sha256=diff.sha256,
                error=error,
            )
        )
    except Exception:
        reason = RepairStopReason.PERSISTENCE_ERROR
        error = "Repair state could not be persisted."
    return build_result(
        request=request,
        scope=scope,
        reason=reason,
        status=status,
        diff=diff,
        baseline=baseline,
        final=final,
        attempts=attempts,
        error=error,
    )


def persistence_result(
    *,
    request: RepairRequest,
    scope: RepairScope,
    status: GitStatusSnapshot,
    diff: GitDiffResult,
    baseline: RepairTestSummary,
    final: RepairTestSummary,
    attempts: tuple[RepairAttemptRecord, ...],
) -> RepairResult:
    return build_result(
        request=request,
        scope=scope,
        reason=RepairStopReason.PERSISTENCE_ERROR,
        status=status,
        diff=diff,
        baseline=baseline,
        final=final,
        attempts=attempts,
        error="Repair state could not be persisted.",
    )


def build_result(
    *,
    request: RepairRequest,
    scope: RepairScope,
    reason: RepairStopReason,
    status: GitStatusSnapshot,
    diff: GitDiffResult,
    baseline: RepairTestSummary | None = None,
    final: RepairTestSummary | None = None,
    attempts: tuple[RepairAttemptRecord, ...] = (),
    error: str | None = None,
) -> RepairResult:
    return RepairResult(
        repair_id=request.repair_id,
        stop_reason=reason,
        editable_paths=scope.editable_paths,
        scope_sha256=scope.sha256,
        baseline_test=baseline,
        final_test=final,
        attempts=attempts,
        final_status_sha256=status.sha256,
        final_diff_sha256=diff.sha256,
        error=error,
    )
