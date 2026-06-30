from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from typing import cast

from mini_code_agent.agent.models import AgentResult, StopReason
from mini_code_agent.git.client import GitService
from mini_code_agent.git.models import GitDiffResult, GitStatusSnapshot
from mini_code_agent.repair.approval import RepairApprovalHandler
from mini_code_agent.repair.events import (
    NullRepairJournal,
    RepairAttemptCompleted,
    RepairAttemptStarted,
    RepairJournal,
    RepairStarted,
    RepairVerificationStarted,
)
from mini_code_agent.repair.evidence import (
    RepairTestRunner,
    repair_state_is_scoped,
    repository_is_clean,
    staged_diff_is_empty,
    test_passed,
    test_repairable,
    test_summary,
)
from mini_code_agent.repair.fingerprint import scope_sha256
from mini_code_agent.repair.models import (
    RepairAttemptRecord,
    RepairLimits,
    RepairPreview,
    RepairRequest,
    RepairResult,
    RepairStopReason,
    RepairTestSummary,
    RepairWorkerRequest,
)
from mini_code_agent.repair.scope import RepairScope
from mini_code_agent.repair.terminal import (
    build_result,
    persistence_result,
    stop_repair,
)
from mini_code_agent.repair.worker import RepairWorker
from mini_code_agent.testing.pytest_runner import PytestRunner
from mini_code_agent.tools.run_tests import RunTestsTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary

_UNKNOWN_SHA256 = "0" * 64


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
        if not repository_is_clean(status, staged, unstaged):
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
            return build_result(
                request=request,
                scope=scope,
                reason=RepairStopReason.PERSISTENCE_ERROR,
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
        summary = test_summary(test_result)
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
        if elapsed_ms >= self._limits.max_elapsed_seconds * 1000:
            return self._stop(
                request,
                scope,
                RepairStopReason.TIME_LIMIT,
                status=post_status,
                diff=post_diff,
                baseline=summary,
                final=summary,
                elapsed_ms=elapsed_ms,
                error="Repair elapsed-time budget was exhausted.",
            )
        if test_passed(test_result):
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
        if not test_repairable(test_result):
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

        return await self._run_attempts(
            request=request,
            scope=scope,
            targets=targets,
            baseline_status=status,
            status=post_status,
            diff=post_diff,
            baseline=summary,
            started=started,
        )

    async def _run_attempts(
        self,
        *,
        request: RepairRequest,
        scope: RepairScope,
        targets: tuple[str, ...],
        baseline_status: GitStatusSnapshot,
        status: GitStatusSnapshot,
        diff: GitDiffResult,
        baseline: RepairTestSummary,
        started: float,
    ) -> RepairResult:
        current_test = baseline
        current_status = status
        current_diff = diff
        attempts: list[RepairAttemptRecord] = []
        failure_counts: Counter[str] = Counter()
        if baseline.failure_sha256 is not None:
            failure_counts[baseline.failure_sha256] = 1

        for attempt in range(1, self._limits.max_attempts + 1):
            if self._time_exceeded(started):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TIME_LIMIT,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair elapsed-time budget was exhausted.",
                )
            failure = current_test.failure_sha256
            if failure is None:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TEST_INFRASTRUCTURE_ERROR,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair diagnostics lost their failure fingerprint.",
                )
            try:
                self._journal.append(
                    RepairAttemptStarted(
                        repair_id=request.repair_id,
                        attempt=attempt,
                        failure_sha256=failure,
                    )
                )
            except Exception:
                return persistence_result(
                    request=request,
                    scope=scope,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                )

            worker_request = RepairWorkerRequest(
                repair_id=request.repair_id,
                attempt=attempt,
                max_attempts=self._limits.max_attempts,
                remaining_attempts=self._limits.max_attempts - attempt + 1,
                user_prompt=request.user_prompt,
                system_prompt=request.system_prompt,
                editable_paths=scope.editable_paths,
                last_test=current_test,
                remaining_elapsed_ms=self._remaining_elapsed_ms(started),
                remaining_patch_bytes=max(
                    0,
                    self._limits.max_patch_bytes - current_diff.byte_count,
                ),
            )
            try:
                worker_candidate = cast(
                    object,
                    await self._worker.run(worker_request),
                )
            except Exception:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.WORKER_FAILED,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair worker failed.",
                )
            if (
                not isinstance(worker_candidate, AgentResult)
                or worker_candidate.stop_reason is not StopReason.COMPLETED
            ):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.WORKER_FAILED,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair worker did not complete one attempt.",
                )
            worker_result = worker_candidate

            try:
                post_status = await self._git.status()
                staged = await self._git.diff(staged=True)
                post_diff = await self._git.diff(staged=False)
            except Exception:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.SCOPE_VIOLATION,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair Git evidence could not be collected.",
                )
            if not staged_diff_is_empty(staged):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.SCOPE_VIOLATION,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair worker changed the Git index.",
                )
            if (
                post_diff.byte_count == 0
                or not post_diff.patch
                or post_diff.sha256 == current_diff.sha256
            ):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.NO_PROGRESS,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair worker produced no new patch evidence.",
                )
            if not repair_state_is_scoped(
                baseline_status,
                post_status,
                post_diff,
                scope,
            ):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.SCOPE_VIOLATION,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair worker changed repository state outside its scope.",
                )
            if post_diff.byte_count > self._limits.max_patch_bytes:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.PATCH_LIMIT,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair patch exceeded the configured byte budget.",
                )
            try:
                post_scope = RepairScope.create(
                    self._workspace,
                    scope.editable_paths,
                )
            except Exception:
                post_scope = None
            if post_scope != scope:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.SCOPE_VIOLATION,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair worker changed an editable file identity.",
                )
            if self._time_exceeded(started):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TIME_LIMIT,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair elapsed-time budget was exhausted.",
                )
            try:
                self._journal.append(
                    RepairVerificationStarted(
                        repair_id=request.repair_id,
                        attempt=attempt,
                        patch_sha256=post_diff.sha256,
                        patch_bytes=post_diff.byte_count,
                    )
                )
            except Exception:
                return persistence_result(
                    request=request,
                    scope=scope,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                )

            try:
                test_result = await self._tests.run(targets)
                after_test_status = await self._git.status()
                after_test_diff = await self._git.diff(staged=False)
            except Exception:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TEST_INFRASTRUCTURE_ERROR,
                    status=post_status,
                    diff=post_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Repair verification test execution failed.",
                )
            try:
                after_test_scope = RepairScope.create(
                    self._workspace,
                    scope.editable_paths,
                )
            except Exception:
                after_test_scope = None
            if (
                after_test_status.sha256 != post_status.sha256
                or after_test_diff.sha256 != post_diff.sha256
                or after_test_scope != scope
            ):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TEST_MUTATED_REPOSITORY,
                    status=after_test_status,
                    diff=after_test_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=_elapsed_ms(self._clock(), started),
                    error="Project tests changed the repository.",
                )

            summary = test_summary(test_result)
            elapsed_ms = _elapsed_ms(self._clock(), started)
            record = RepairAttemptRecord(
                attempt=attempt,
                worker_run_id=worker_result.run_id,
                worker_stop_reason=worker_result.stop_reason,
                patch_sha256=post_diff.sha256,
                patch_bytes=post_diff.byte_count,
                test=summary,
                failure_sha256=summary.failure_sha256,
                elapsed_ms=elapsed_ms,
            )
            try:
                self._journal.append(
                    RepairAttemptCompleted(
                        repair_id=request.repair_id,
                        attempt=attempt,
                        worker_run_id=worker_result.run_id,
                        worker_stop_reason=worker_result.stop_reason,
                        patch_sha256=post_diff.sha256,
                        patch_bytes=post_diff.byte_count,
                        test_status=summary.status,
                        report_status=summary.report_status,
                        counts=summary.counts,
                        failure_sha256=summary.failure_sha256,
                        elapsed_ms=elapsed_ms,
                    )
                )
            except Exception:
                return persistence_result(
                    request=request,
                    scope=scope,
                    status=after_test_status,
                    diff=after_test_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                )
            attempts.append(record)
            current_status = after_test_status
            current_diff = after_test_diff
            current_test = summary

            if test_passed(test_result):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.REPAIRED,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=elapsed_ms,
                )
            if not test_repairable(test_result):
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TEST_INFRASTRUCTURE_ERROR,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=elapsed_ms,
                    error="Repair verification diagnostics are not repairable.",
                )
            current_failure = summary.failure_sha256
            if current_failure is None:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.TEST_INFRASTRUCTURE_ERROR,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=elapsed_ms,
                    error="Repair verification fingerprint is unavailable.",
                )
            failure_counts[current_failure] += 1
            if failure_counts[current_failure] >= self._limits.max_same_failure:
                return self._stop(
                    request,
                    scope,
                    RepairStopReason.REPEATED_FAILURE,
                    status=current_status,
                    diff=current_diff,
                    baseline=baseline,
                    final=current_test,
                    attempts=tuple(attempts),
                    elapsed_ms=elapsed_ms,
                    error="Repair repeated the same normalized failure.",
                )

        return self._stop(
            request,
            scope,
            RepairStopReason.MAX_ATTEMPTS,
            status=current_status,
            diff=current_diff,
            baseline=baseline,
            final=current_test,
            attempts=tuple(attempts),
            elapsed_ms=_elapsed_ms(self._clock(), started),
            error="Repair reached the configured attempt limit.",
        )

    def _time_exceeded(self, started: float) -> bool:
        return self._clock() - started >= self._limits.max_elapsed_seconds

    def _remaining_elapsed_ms(self, started: float) -> int:
        remaining = self._limits.max_elapsed_seconds - (self._clock() - started)
        return min(3_600_000, max(0, int(remaining * 1000)))

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
            final_status_sha256=status.sha256 if status is not None else _UNKNOWN_SHA256,
            final_diff_sha256=diff.sha256 if diff is not None else _UNKNOWN_SHA256,
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
        attempts: tuple[RepairAttemptRecord, ...] = (),
        elapsed_ms: int,
        error: str | None = None,
    ) -> RepairResult:
        del elapsed_ms
        return stop_repair(
            journal=self._journal,
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


def _elapsed_ms(current: float, started: float) -> int:
    return min(3_700_000, max(0, int((current - started) * 1000)))
