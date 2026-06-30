from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mini_code_agent.git.models import (
    GitDiffMode,
    GitDiffResult,
    GitEntryKind,
    GitStatusSnapshot,
)
from mini_code_agent.repair.fingerprint import failure_sha256
from mini_code_agent.repair.models import RepairTestSummary
from mini_code_agent.repair.scope import RepairScope
from mini_code_agent.testing.models import (
    PytestExecutionStatus,
    PytestLimits,
    PytestProfile,
    PytestReportStatus,
    PytestRunResult,
)


class RepairTestRunner(Protocol):
    @property
    def workspace_root(self) -> Path: ...

    @property
    def profile(self) -> PytestProfile: ...

    @property
    def limits(self) -> PytestLimits: ...

    def preview_argv(self, targets: tuple[str, ...]) -> tuple[str, ...]: ...

    async def run(self, targets: tuple[str, ...]) -> PytestRunResult: ...


def repository_is_clean(
    status: GitStatusSnapshot,
    staged: GitDiffResult,
    unstaged: GitDiffResult,
) -> bool:
    return (
        not status.entries
        and staged.byte_count == 0
        and not staged.patch
        and unstaged.byte_count == 0
        and not unstaged.patch
    )


def staged_diff_is_empty(staged: GitDiffResult) -> bool:
    return staged.mode is GitDiffMode.STAGED and staged.byte_count == 0 and not staged.patch


def repair_state_is_scoped(
    baseline: GitStatusSnapshot,
    current: GitStatusSnapshot,
    current_diff: GitDiffResult,
    scope: RepairScope,
) -> bool:
    if (
        current_diff.mode is not GitDiffMode.UNSTAGED
        or not current.entries
        or baseline.branch_oid != current.branch_oid
        or baseline.branch_head != current.branch_head
        or baseline.branch_upstream != current.branch_upstream
        or baseline.ahead != current.ahead
        or baseline.behind != current.behind
    ):
        return False
    editable = frozenset(scope.editable_paths)
    return all(
        entry.kind is GitEntryKind.ORDINARY
        and entry.index_status == "."
        and entry.worktree_status == "M"
        and entry.submodule == "N..."
        and entry.path in editable
        for entry in current.entries
    )


def test_passed(result: PytestRunResult) -> bool:
    return (
        result.status is PytestExecutionStatus.PASSED
        and result.report_status is PytestReportStatus.COMPLETE
    )


def test_repairable(result: PytestRunResult) -> bool:
    return (
        result.status is PytestExecutionStatus.FAILED
        and result.report_status is PytestReportStatus.COMPLETE
        and result.counts.failed + result.counts.errors > 0
        and bool(result.diagnostics)
    )


def test_summary(result: PytestRunResult) -> RepairTestSummary:
    repairable = test_repairable(result)
    diagnostics = result.diagnostics[:100] if repairable else ()
    return RepairTestSummary(
        status=result.status,
        report_status=result.report_status,
        counts=result.counts,
        diagnostics=diagnostics,
        diagnostics_truncated=(
            result.diagnostics_truncated or len(result.diagnostics) > len(diagnostics)
        ),
        failure_sha256=failure_sha256(result) if repairable else None,
    )
