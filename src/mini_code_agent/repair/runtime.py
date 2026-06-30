from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from mini_code_agent.git.client import GitService
from mini_code_agent.git.models import GitDiffResult, GitStatusSnapshot
from mini_code_agent.repair.approval import RepairApprovalHandler
from mini_code_agent.repair.events import (
    NullRepairJournal,
    RepairJournal,
    RepairStarted,
    RepairStopped,
)
from mini_code_agent.repair.fingerprint import failure_sha256, scope_sha256
from mini_code_agent.repair.models import (
    RepairLimits,
    RepairPreview,
    RepairRequest,
    RepairResult,
    RepairStopReason,
    RepairTestSummary,
)
from mini_code_agent.repair.scope import RepairScope
from mini_code_agent.repair.worker import RepairWorker
from mini_code_agent.testing.models import (
    PytestExecutionStatus,
    PytestLimits,
    PytestProfile,
    PytestReportStatus,
    PytestRunResult,
)
from mini_code_agent.testing.pytest_runner import PytestRunner
from mini_code_agent.tools.run_tests import RunTestsTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary

_EMPTY_SHA256 = scope_sha256(())


class RepairTestRunner(Protocol):
    @property
    def workspace_root(self) -> Path: ...

    @property
    def profile(self) -> PytestProfile: ...

    @property
    def limits(self) -> PytestLimits: ...

    def preview_argv(self, targets: tuple[str, ...]) -> tuple[str, ...]: ...

    async def run(self, targets: tuple[str, ...]) -> PytestRunResult: ...


class RepairRuntime:
    def __init__(
        self,
        workspace: WorkspaceBoundary,
        git: GitService,
        tests: RepairTestRunner,
        worker: RepairWorker,
        approval: RepairApprovalHandler,
        *,
        journal: RepairJournal | None = None,
        limits: RepairLimits | None = None,
        allow_volatile: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if workspace.root != git.workspace_root or workspace.root != tests.workspace_root:
            raise ValueError("Repair Workspace, Git, and Pytest roots must match.")
        if journal is None and not allow_volatile:
            raise ValueError("Repair journal is required unless volatile mode is explicit.")
        self._workspace = workspace
        self._git = git
        self._tests = tests
        self._worker = worker
        self._approval = approval
        self._journal = journal or NullRepairJournal()
        self._limits = limits or RepairLimits()
        self._clock = clock
        self._test_tool = RunTestsTool(
            workspace,
            cast(PytestRunner, tests),
        )

    async def run(self, request: RepairRequest) -> RepairResult:
        started = self._clock()
        try:
            scope = RepairScope.create(self._workspace, request.editable_paths)
            targets = self._test_tool.prepare_targets(
                request.test_targets if request.test_targets else None
            )
        except Exception:
            return self._early_result(
                request,
                RepairStopReason.INVALID_SCOPE,
                error="Repair scope or test targets are invalid.",
            )

        preview = RepairPreview(
            repair_id=request.repair_id,
            test_targets=targets,
            editable_paths=scope.editable_paths,
            scope_sha256=scope.sha256,
            max_attempts=self._limits.max_attempts,
            max_elapsed_seconds=self._limits.max_elapsed_seconds,
            max_patch_bytes=self._limits.max_patch_bytes,
            reason=request.reason,
        )
        try:
            approved = cast(object, await self._approval.approve(preview))
        except Exception:
            approved = False
        if not isinstance(approved, bool) or not approved:
            return self._early_result(
                request,
                RepairStopReason.NOT_APPROVED,
                scope=scope,
                error="Repair session was not approved.",
            )

        try:
            current_scope = RepairScope.create(
                self._workspace,
                request.editable_paths,
            )
            status = await self._git.status()
            staged = await self._git.diff(staged=True)
            unstaged = await self._git.diff(staged=False)
        except Exception:
            return self._early_result(
                request,
                RepairStopReason.INVALID_SCOPE,
                scope=scope,
                error="Repair admission evidence could not be collected.",
            )
        if current_scope != scope:
            return self._early_result(
                request,
                RepairStopReason.INVALID_SCOPE,
                scope=scope,
                status=status,
                diff=unstaged,
                error="Repair scope changed during approval.",
            )
        if not _repository_is_clean(status, staged, unstaged):
            return self._early_result(
                request,
                RepairStopReason.DIRTY_REPOSITORY,
                scope=scope,
                status=status,
                diff=unstaged,
                error="Repair requires a clean repository.",
            )
        try:
            tracked = await self._git.tracked_paths(scope.editable_paths)
        except Exception:
            tracked = ()
        if tracked != scope.editable_paths or self._worker.scope_sha256 != scope.sha256:
            return self._early_result(
                request,
                RepairStopReason.INVALID_SCOPE,
                scope=scope,
                status=status,
                diff=unstaged,
                error="Repair scope is not an exact tracked worker scope.",
            )

        try:
            self._journal.append(
                RepairStarted(
                    repair_id=request.repair_id,
                    scope_sha256=scope.sha256,
                    max_attempts=self._limits.max_attempts,
                    test_target_count=len(targets),
                    editable_path_count=len(scope.editable_paths),
                )
            )
        except Exception:
            return self._result(
                request,
                scope,
                RepairStopReason.PERSISTENCE_ERROR,
                status=status,
                diff=unstaged,
                error="Repair state could not be persisted.",
            )

        try:
            test_result = await self._tests.run(targets)
            post_status = await self._git.status()
            post_diff = await self._git.diff(staged=False)
        except Exception:
            return self._stop(
                request,
                scope,
                RepairStopReason.TEST_INFRASTRUCTURE_ERROR,
                status=status,
                diff=unstaged,
                elapsed_ms=_elapsed_ms(self._clock(), started),
                error="Repair baseline test execution failed.",
            )
        summary = _test_summary(test_result)
        elapsed_ms = _elapsed_ms(self._clock(), started)
        if post_status.sha256 != status.sha256 or post_diff.sha256 != unstaged.sha256:
            return self._stop(
                request,
                scope,
                RepairStopReason.TEST_MUTATED_REPOSITORY,
                status=post_status,
                diff=post_diff,
                baseline=summary,
                final=summary,
                elapsed_ms=elapsed_ms,
                error="Project tests changed the repository.",
            )
        if _test_passed(test_result):
            return self._stop(
                request,
                scope,
                RepairStopReason.ALREADY_PASSING,
                status=post_status,
                diff=post_diff,
                baseline=summary,
                final=summary,
                elapsed_ms=elapsed_ms,
            )
        if not _test_repairable(test_result):
            return self._stop(
                request,
                scope,
                RepairStopReason.TEST_INFRASTRUCTURE_ERROR,
                status=post_status,
                diff=post_diff,
                baseline=summary,
                final=summary,
                elapsed_ms=elapsed_ms,
                error="Baseline test diagnostics are not repairable.",
            )

        return self._stop(
            request,
            scope,
            RepairStopReason.MAX_ATTEMPTS,
            status=post_status,
            diff=post_diff,
            baseline=summary,
            final=summary,
            elapsed_ms=elapsed_ms,
        )

    def _early_result(
        self,
        request: RepairRequest,
        reason: RepairStopReason,
        *,
        scope: RepairScope | None = None,
        status: GitStatusSnapshot | None = None,
        diff: GitDiffResult | None = None,
        error: str | None = None,
    ) -> RepairResult:
        editable_paths = scope.editable_paths if scope is not None else request.editable_paths
        return RepairResult(
            repair_id=request.repair_id,
            stop_reason=reason,
            editable_paths=editable_paths,
            scope_sha256=(
                scope.sha256
                if scope is not None
                else scope_sha256(tuple(sorted(request.editable_paths)))
            ),
            final_status_sha256=status.sha256 if status is not None else _EMPTY_SHA256,
            final_diff_sha256=diff.sha256 if diff is not None else _EMPTY_SHA256,
            error=error,
        )

    def _stop(
        self,
        request: RepairRequest,
        scope: RepairScope,
        reason: RepairStopReason,
        *,
        status: GitStatusSnapshot,
        diff: GitDiffResult,
        baseline: RepairTestSummary | None = None,
        final: RepairTestSummary | None = None,
        elapsed_ms: int,
        error: str | None = None,
    ) -> RepairResult:
        del elapsed_ms
        try:
            self._journal.append(
                RepairStopped(
                    repair_id=request.repair_id,
                    reason=reason,
                    attempts=0,
                    final_status_sha256=status.sha256,
                    final_diff_sha256=diff.sha256,
                    error=error,
                )
            )
        except Exception:
            reason = RepairStopReason.PERSISTENCE_ERROR
            error = "Repair state could not be persisted."
        return self._result(
            request,
            scope,
            reason,
            status=status,
            diff=diff,
            baseline=baseline,
            final=final,
            error=error,
        )

    @staticmethod
    def _result(
        request: RepairRequest,
        scope: RepairScope,
        reason: RepairStopReason,
        *,
        status: GitStatusSnapshot,
        diff: GitDiffResult,
        baseline: RepairTestSummary | None = None,
        final: RepairTestSummary | None = None,
        error: str | None = None,
    ) -> RepairResult:
        return RepairResult(
            repair_id=request.repair_id,
            stop_reason=reason,
            editable_paths=scope.editable_paths,
            scope_sha256=scope.sha256,
            baseline_test=baseline,
            final_test=final,
            final_status_sha256=status.sha256,
            final_diff_sha256=diff.sha256,
            error=error,
        )


def _repository_is_clean(
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


def _test_passed(result: PytestRunResult) -> bool:
    return (
        result.status is PytestExecutionStatus.PASSED
        and result.report_status is PytestReportStatus.COMPLETE
    )


def _test_repairable(result: PytestRunResult) -> bool:
    return (
        result.status is PytestExecutionStatus.FAILED
        and result.report_status is PytestReportStatus.COMPLETE
        and result.counts.failed + result.counts.errors > 0
        and bool(result.diagnostics)
    )


def _test_summary(result: PytestRunResult) -> RepairTestSummary:
    repairable = _test_repairable(result)
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


def _elapsed_ms(current: float, started: float) -> int:
    return min(3_700_000, max(0, int((current - started) * 1000)))
