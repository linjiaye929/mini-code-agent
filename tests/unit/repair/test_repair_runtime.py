from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from mini_code_agent.agent.models import AgentResult, StopReason
from mini_code_agent.git.models import (
    GitDiffMode,
    GitDiffResult,
    GitEntryKind,
    GitStatusEntry,
    GitStatusSnapshot,
    status_sha256,
)
from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.repair.approval import StaticRepairApprovalHandler
from mini_code_agent.repair.events import RecordingRepairJournal, RepairEvent
from mini_code_agent.repair.fingerprint import scope_sha256
from mini_code_agent.repair.models import (
    RepairLimits,
    RepairRequest,
    RepairStopReason,
    RepairWorkerRequest,
)
from mini_code_agent.repair.runtime import RepairRuntime
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
from mini_code_agent.workspace.boundary import WorkspaceBoundary

EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class RecordingGit:
    def __init__(
        self,
        root: Path,
        *,
        statuses: Iterable[GitStatusSnapshot] = (),
        staged_diffs: Iterable[GitDiffResult] = (),
        unstaged_diffs: Iterable[GitDiffResult] = (),
        tracked: tuple[str, ...] = ("src/app.py",),
    ) -> None:
        self.workspace_root = root.resolve()
        self._statuses = iter(statuses)
        self._staged_diffs = iter(staged_diffs)
        self._unstaged_diffs = iter(unstaged_diffs)
        self._tracked = tracked
        self.status_calls = 0
        self.diff_calls: list[bool] = []
        self.tracked_calls: list[tuple[str, ...]] = []

    async def status(self) -> GitStatusSnapshot:
        self.status_calls += 1
        return next(self._statuses)

    async def diff(self, *, staged: bool = False) -> GitDiffResult:
        self.diff_calls.append(staged)
        return next(self._staged_diffs if staged else self._unstaged_diffs)

    async def tracked_paths(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        self.tracked_calls.append(paths)
        return self._tracked


class RecordingTests:
    def __init__(
        self,
        root: Path,
        results: Iterable[PytestRunResult],
    ) -> None:
        self.workspace_root = root.resolve()
        self.profile = PytestProfile(
            python_executable=Path(sys.executable),
            default_targets=("tests",),
        )
        self.limits = PytestLimits()
        self._results = iter(results)
        self.calls: list[tuple[str, ...]] = []

    async def run(self, targets: tuple[str, ...]) -> PytestRunResult:
        self.calls.append(targets)
        return next(self._results)

    def preview_argv(self, targets: tuple[str, ...]) -> tuple[str, ...]:
        return ("python", "-I", "-m", "pytest", "--", *targets)


class FileTypeMutatingTests(RecordingTests):
    def __init__(
        self,
        root: Path,
        results: Iterable[PytestRunResult],
        *,
        mutate_on_call: int,
    ) -> None:
        super().__init__(root, results)
        self._root = root
        self._mutate_on_call = mutate_on_call

    async def run(self, targets: tuple[str, ...]) -> PytestRunResult:
        result = await super().run(targets)
        if len(self.calls) == self._mutate_on_call:
            target = self._root / "src" / "app.py"
            target.unlink()
            target.mkdir()
        return result


class RecordingWorker:
    def __init__(
        self,
        scope_sha256: str,
        responses: Iterable[AgentResult | BaseException] = (),
    ) -> None:
        self._scope_sha256 = scope_sha256
        self._responses = iter(responses)
        self.calls: list[RepairWorkerRequest] = []

    @property
    def scope_sha256(self) -> str:
        return self._scope_sha256

    async def run(self, request: RepairWorkerRequest) -> AgentResult:
        self.calls.append(request)
        default = AgentResult(
            run_id=f"{request.repair_id}-attempt-{request.attempt}",
            messages=(),
            stop_reason=StopReason.COMPLETED,
            turns=1,
            tool_calls=1,
            usage=TokenUsage(),
        )
        response = next(self._responses, default)
        if isinstance(response, BaseException):
            raise response
        return response


class FailingApproval:
    async def approve(self, preview: object) -> bool:
        del preview
        raise RuntimeError("secret approval failure")


class FailingJournal:
    def __init__(self) -> None:
        self.calls: list[RepairEvent] = []

    def append(self, event: RepairEvent) -> None:
        self.calls.append(event)
        raise RuntimeError("secret journal failure")


class FailingAtJournal(RecordingRepairJournal):
    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self._fail_at = fail_at
        self._calls = 0

    def append(self, event: RepairEvent) -> None:
        self._calls += 1
        if self._calls == self._fail_at:
            raise RuntimeError("secret journal failure")
        super().append(event)


class SequenceClock:
    def __init__(self, values: Iterable[float]) -> None:
        self._values = iter(values)
        self._last = 0.0

    def __call__(self) -> float:
        self._last = next(self._values, self._last)
        return self._last


def workspace(tmp_path: Path) -> WorkspaceBoundary:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    return WorkspaceBoundary(tmp_path)


def request(**overrides: object) -> RepairRequest:
    values: dict[str, object] = {
        "repair_id": "repair-1",
        "user_prompt": "Fix the failing test.",
        "test_targets": ("tests",),
        "editable_paths": ("src/app.py",),
        "reason": "Repair the regression.",
    }
    values.update(overrides)
    return RepairRequest.model_validate(values)


def clean_status() -> GitStatusSnapshot:
    return make_status(())


def make_status(entries: tuple[GitStatusEntry, ...]) -> GitStatusSnapshot:
    sha256 = status_sha256(
        branch_oid="a" * 40,
        branch_head="main",
        branch_upstream=None,
        ahead=0,
        behind=0,
        entries=entries,
    )
    return GitStatusSnapshot(
        branch_oid="a" * 40,
        branch_head="main",
        branch_upstream=None,
        ahead=0,
        behind=0,
        entries=entries,
        sha256=sha256,
    )


def ordinary(
    *,
    index_status: str = ".",
    worktree_status: str = "M",
    submodule: str = "N...",
) -> GitStatusEntry:
    return GitStatusEntry(
        kind=GitEntryKind.ORDINARY,
        index_status=index_status,
        worktree_status=worktree_status,
        path="src/app.py",
        submodule=submodule,
    )


def diff(
    patch: str = "",
    *,
    mode: GitDiffMode = GitDiffMode.UNSTAGED,
) -> GitDiffResult:
    encoded = patch.encode("utf-8")
    return GitDiffResult(
        mode=mode,
        patch=patch,
        byte_count=len(encoded),
        char_count=len(patch),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def passed_tests() -> PytestRunResult:
    return pytest_result(
        status=PytestExecutionStatus.PASSED,
        report_status=PytestReportStatus.COMPLETE,
        exit_code=0,
        counts=PytestCounts(total=1, passed=1, failed=0, errors=0, skipped=0),
    )


def failed_tests() -> PytestRunResult:
    return pytest_result(
        status=PytestExecutionStatus.FAILED,
        report_status=PytestReportStatus.COMPLETE,
        exit_code=1,
        counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
        diagnostics=(
            PytestDiagnostic(
                outcome=PytestDiagnosticOutcome.FAILURE,
                test_name="test_value",
                file="tests/test_app.py",
                line=2,
                message="assert 1 == 2",
                details="AssertionError",
            ),
        ),
    )


def pytest_result(
    *,
    status: PytestExecutionStatus,
    report_status: PytestReportStatus,
    exit_code: int | None,
    counts: PytestCounts | None = None,
    diagnostics: tuple[PytestDiagnostic, ...] = (),
) -> PytestRunResult:
    return PytestRunResult(
        status=status,
        report_status=report_status,
        exit_code=exit_code,
        duration_ms=10,
        stdout="",
        stderr="",
        timed_out=status is PytestExecutionStatus.TIMED_OUT,
        output_limit_exceeded=(status is PytestExecutionStatus.OUTPUT_LIMIT_EXCEEDED),
        counts=counts or PytestCounts.empty(),
        diagnostics=diagnostics,
    )


def admitted_dependencies(
    root: Path,
    test_results: Iterable[PytestRunResult],
    *,
    post_status: GitStatusSnapshot | None = None,
    post_diff: GitDiffResult | None = None,
) -> tuple[RecordingGit, RecordingTests]:
    git = RecordingGit(
        root,
        statuses=(clean_status(), post_status or clean_status()),
        staged_diffs=(diff(mode=GitDiffMode.STAGED),),
        unstaged_diffs=(diff(), post_diff or diff()),
    )
    return git, RecordingTests(root, test_results)


def test_constructor_requires_matching_roots_and_explicit_journal(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    git = RecordingGit(other)
    tests = RecordingTests(tmp_path, ())
    worker = RecordingWorker("a" * 64)
    approval = StaticRepairApprovalHandler(approved=True)

    with pytest.raises(ValueError, match="roots must match"):
        RepairRuntime(boundary, git, tests, worker, approval, allow_volatile=True)
    with pytest.raises(ValueError, match="journal is required"):
        RepairRuntime(
            boundary,
            RecordingGit(tmp_path),
            tests,
            worker,
            approval,
        )


@pytest.mark.asyncio
async def test_invalid_scope_stops_before_approval_or_dependencies(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    approval = StaticRepairApprovalHandler(approved=True)
    git = RecordingGit(tmp_path)
    tests = RecordingTests(tmp_path, ())
    worker = RecordingWorker("a" * 64)
    runtime = RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        approval,
        allow_volatile=True,
    )

    result = await runtime.run(request(editable_paths=("src/missing.py",)))

    assert result.stop_reason is RepairStopReason.INVALID_SCOPE
    assert approval.requests == []
    assert git.status_calls == 0
    assert tests.calls == []
    assert worker.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "approval",
    [StaticRepairApprovalHandler(approved=False), FailingApproval()],
)
async def test_rejected_or_failed_approval_has_zero_git_test_or_worker_calls(
    tmp_path: Path,
    approval: object,
) -> None:
    boundary = workspace(tmp_path)
    git = RecordingGit(tmp_path)
    tests = RecordingTests(tmp_path, ())
    worker = RecordingWorker("a" * 64)
    runtime = RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        approval,  # type: ignore[arg-type]
        allow_volatile=True,
    )

    result = await runtime.run(request())

    assert result.stop_reason is RepairStopReason.NOT_APPROVED
    assert git.status_calls == 0
    assert tests.calls == []
    assert worker.calls == []
    assert "secret" not in (result.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "staged", "unstaged"),
    (
        (
            make_status(
                (
                    GitStatusEntry(
                        kind=GitEntryKind.UNTRACKED,
                        index_status="?",
                        worktree_status="?",
                        path="new.py",
                    ),
                )
            ),
            diff(mode=GitDiffMode.STAGED),
            diff(),
        ),
        (
            make_status((ordinary(index_status="M"),)),
            diff("staged", mode=GitDiffMode.STAGED),
            diff(),
        ),
        (make_status((ordinary(),)), diff(mode=GitDiffMode.STAGED), diff("unstaged")),
        (
            make_status(
                (
                    GitStatusEntry(
                        kind=GitEntryKind.RENAMED,
                        index_status="R",
                        worktree_status=".",
                        path="src/new.py",
                        original_path="src/app.py",
                        submodule="N...",
                    ),
                )
            ),
            diff(mode=GitDiffMode.STAGED),
            diff(),
        ),
        (
            make_status(
                (
                    GitStatusEntry(
                        kind=GitEntryKind.UNMERGED,
                        index_status="U",
                        worktree_status="U",
                        path="src/app.py",
                        submodule="N...",
                    ),
                )
            ),
            diff(mode=GitDiffMode.STAGED),
            diff(),
        ),
        (
            make_status((ordinary(submodule="S.M."),)),
            diff(mode=GitDiffMode.STAGED),
            diff(),
        ),
    ),
)
async def test_dirty_repository_is_rejected_before_tracked_test_or_worker(
    tmp_path: Path,
    status: GitStatusSnapshot,
    staged: GitDiffResult,
    unstaged: GitDiffResult,
) -> None:
    boundary = workspace(tmp_path)
    git = RecordingGit(
        tmp_path,
        statuses=(status,),
        staged_diffs=(staged,),
        unstaged_diffs=(unstaged,),
    )
    tests = RecordingTests(tmp_path, ())
    worker = RecordingWorker("a" * 64)
    runtime = RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        allow_volatile=True,
    )

    result = await runtime.run(request())

    assert result.stop_reason is RepairStopReason.DIRTY_REPOSITORY
    assert git.tracked_calls == []
    assert tests.calls == []
    assert worker.calls == []


@pytest.mark.asyncio
async def test_untracked_scope_or_worker_scope_mismatch_fails_before_test(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    approval = StaticRepairApprovalHandler(approved=True)
    clean_git = RecordingGit(
        tmp_path,
        statuses=(clean_status(),),
        staged_diffs=(diff(mode=GitDiffMode.STAGED),),
        unstaged_diffs=(diff(),),
        tracked=(),
    )
    tests = RecordingTests(tmp_path, ())
    runtime = RepairRuntime(
        boundary,
        clean_git,
        tests,
        RecordingWorker("a" * 64),
        approval,
        allow_volatile=True,
    )

    untracked = await runtime.run(request())

    assert untracked.stop_reason is RepairStopReason.INVALID_SCOPE
    assert tests.calls == []

    clean_git = RecordingGit(
        tmp_path,
        statuses=(clean_status(),),
        staged_diffs=(diff(mode=GitDiffMode.STAGED),),
        unstaged_diffs=(diff(),),
    )
    mismatch = await RepairRuntime(
        boundary,
        clean_git,
        tests,
        RecordingWorker("f" * 64),
        approval,
        allow_volatile=True,
    ).run(request())

    assert mismatch.stop_reason is RepairStopReason.INVALID_SCOPE
    assert tests.calls == []


@pytest.mark.asyncio
async def test_required_start_journal_failure_stops_before_baseline(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    git = RecordingGit(
        tmp_path,
        statuses=(clean_status(),),
        staged_diffs=(diff(mode=GitDiffMode.STAGED),),
        unstaged_diffs=(diff(),),
    )
    tests = RecordingTests(tmp_path, ())
    runtime = RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=FailingJournal(),
    )

    result = await runtime.run(request())

    assert result.stop_reason is RepairStopReason.PERSISTENCE_ERROR
    assert tests.calls == []
    assert "secret" not in (result.error or "")


@pytest.mark.asyncio
async def test_complete_passing_baseline_stops_without_worker(tmp_path: Path) -> None:
    boundary = workspace(tmp_path)
    expected_scope = scope_sha256(("src/app.py",))
    git, tests = admitted_dependencies(tmp_path, (passed_tests(),))
    worker = RecordingWorker(expected_scope)
    journal = RecordingRepairJournal()
    runtime = RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=journal,
    )

    result = await runtime.run(request())

    assert result.stop_reason is RepairStopReason.ALREADY_PASSING
    assert result.succeeded is True
    assert result.baseline_test == result.final_test
    assert result.final_test is not None
    assert result.final_test.status is PytestExecutionStatus.PASSED
    assert tests.calls == [("tests",)]
    assert worker.calls == []
    assert tuple(event.type for event in journal.events) == (
        "repair_started",
        "repair_stopped",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test",
    (
        pytest_result(
            status=PytestExecutionStatus.TIMED_OUT,
            report_status=PytestReportStatus.MISSING,
            exit_code=None,
        ),
        pytest_result(
            status=PytestExecutionStatus.INTERNAL_ERROR,
            report_status=PytestReportStatus.COMPLETE,
            exit_code=3,
        ),
        pytest_result(
            status=PytestExecutionStatus.NO_TESTS,
            report_status=PytestReportStatus.COMPLETE,
            exit_code=5,
        ),
        pytest_result(
            status=PytestExecutionStatus.PASSED,
            report_status=PytestReportStatus.INVALID,
            exit_code=0,
        ),
    ),
)
async def test_baseline_infrastructure_result_is_not_repaired(
    tmp_path: Path,
    test: PytestRunResult,
) -> None:
    boundary = workspace(tmp_path)
    expected_scope = scope_sha256(("src/app.py",))
    git, tests = admitted_dependencies(tmp_path, (test,))
    worker = RecordingWorker(expected_scope)
    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.TEST_INFRASTRUCTURE_ERROR
    assert worker.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post_status", "post_diff"),
    (
        (make_status((ordinary(),)), diff()),
        (clean_status(), diff("test mutation")),
    ),
)
async def test_baseline_test_repository_mutation_is_detected(
    tmp_path: Path,
    post_status: GitStatusSnapshot,
    post_diff: GitDiffResult,
) -> None:
    boundary = workspace(tmp_path)
    expected_scope = scope_sha256(("src/app.py",))
    git, tests = admitted_dependencies(
        tmp_path,
        (failed_tests(),),
        post_status=post_status,
        post_diff=post_diff,
    )
    worker = RecordingWorker(expected_scope)

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.TEST_MUTATED_REPOSITORY
    assert worker.calls == []


def attempt_git(
    root: Path,
    *,
    attempt_statuses: tuple[GitStatusSnapshot, ...],
    attempt_diffs: tuple[GitDiffResult, ...],
) -> RecordingGit:
    statuses: list[GitStatusSnapshot] = [clean_status(), clean_status()]
    staged: list[GitDiffResult] = [diff(mode=GitDiffMode.STAGED)]
    unstaged: list[GitDiffResult] = [diff(), diff()]
    for status, current_diff in zip(
        attempt_statuses,
        attempt_diffs,
        strict=True,
    ):
        statuses.extend((status, status))
        staged.append(diff(mode=GitDiffMode.STAGED))
        unstaged.extend((current_diff, current_diff))
    return RecordingGit(
        root,
        statuses=statuses,
        staged_diffs=staged,
        unstaged_diffs=unstaged,
    )


def worker_result(
    *,
    run_id: str = "repair-1-attempt-1",
    reason: StopReason = StopReason.COMPLETED,
) -> AgentResult:
    return AgentResult(
        run_id=run_id,
        messages=(),
        stop_reason=reason,
        turns=1,
        tool_calls=1,
        usage=TokenUsage(),
    )


def changed_failure(message: str) -> PytestRunResult:
    original = failed_tests()
    diagnostic = original.diagnostics[0].model_copy(update={"message": message})
    return original.model_copy(update={"diagnostics": (diagnostic,)})


@pytest.mark.asyncio
async def test_one_attempt_repair_succeeds_only_after_trusted_test_pass(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    patch = diff("diff --git a/src/app.py b/src/app.py\n+value = 2\n")
    status = make_status((ordinary(),))
    git = attempt_git(
        tmp_path,
        attempt_statuses=(status,),
        attempt_diffs=(patch,),
    )
    tests = RecordingTests(tmp_path, (failed_tests(), passed_tests()))
    worker = RecordingWorker(scope_sha256(("src/app.py",)))
    journal = RecordingRepairJournal()

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=journal,
    ).run(request())

    assert result.stop_reason is RepairStopReason.REPAIRED
    assert result.succeeded is True
    assert len(result.attempts) == 1
    assert result.attempts[0].patch_sha256 == patch.sha256
    assert result.attempts[0].test.status is PytestExecutionStatus.PASSED
    assert len(worker.calls) == 1
    assert worker.calls[0].attempt == 1
    assert worker.calls[0].last_test.status is PytestExecutionStatus.FAILED
    assert tuple(event.type for event in journal.events) == (
        "repair_started",
        "repair_attempt_started",
        "repair_verification_started",
        "repair_attempt_completed",
        "repair_stopped",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    (
        worker_result(reason=StopReason.MAX_TURNS),
        RuntimeError("secret worker failure"),
    ),
)
async def test_worker_failure_stops_before_git_verification(
    tmp_path: Path,
    response: AgentResult | BaseException,
) -> None:
    boundary = workspace(tmp_path)
    git, tests = admitted_dependencies(tmp_path, (failed_tests(),))
    worker = RecordingWorker(
        scope_sha256(("src/app.py",)),
        responses=(response,),
    )

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.WORKER_FAILED
    assert git.status_calls == 2
    assert tests.calls == [("tests",)]
    assert "secret" not in (result.error or "")


@pytest.mark.asyncio
async def test_worker_without_new_diff_stops_no_progress(tmp_path: Path) -> None:
    boundary = workspace(tmp_path)
    git = attempt_git(
        tmp_path,
        attempt_statuses=(clean_status(),),
        attempt_diffs=(diff(),),
    )
    tests = RecordingTests(tmp_path, (failed_tests(),))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.NO_PROGRESS
    assert tests.calls == [("tests",)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_status",
    (
        make_status((ordinary(index_status="M"),)),
        make_status(
            (
                GitStatusEntry(
                    kind=GitEntryKind.UNTRACKED,
                    index_status="?",
                    worktree_status="?",
                    path="new.py",
                ),
            )
        ),
        make_status(
            (
                GitStatusEntry(
                    kind=GitEntryKind.ORDINARY,
                    index_status=".",
                    worktree_status="D",
                    path="src/app.py",
                    submodule="N...",
                ),
            )
        ),
        make_status(
            (
                GitStatusEntry(
                    kind=GitEntryKind.ORDINARY,
                    index_status=".",
                    worktree_status="M",
                    path="README.md",
                    submodule="N...",
                ),
            )
        ),
        make_status((ordinary(submodule="S.M."),)),
    ),
)
async def test_attempt_rejects_nonordinary_or_out_of_scope_state(
    tmp_path: Path,
    invalid_status: GitStatusSnapshot,
) -> None:
    boundary = workspace(tmp_path)
    patch = diff("changed")
    git = attempt_git(
        tmp_path,
        attempt_statuses=(invalid_status,),
        attempt_diffs=(patch,),
    )
    tests = RecordingTests(tmp_path, (failed_tests(),))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.SCOPE_VIOLATION
    assert tests.calls == [("tests",)]


@pytest.mark.asyncio
async def test_attempt_rejects_staged_or_oversized_patch(tmp_path: Path) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("x" * 11)
    git = RecordingGit(
        tmp_path,
        statuses=(clean_status(), clean_status(), status),
        staged_diffs=(
            diff(mode=GitDiffMode.STAGED),
            diff("staged", mode=GitDiffMode.STAGED),
        ),
        unstaged_diffs=(diff(), diff(), patch),
    )
    tests = RecordingTests(tmp_path, (failed_tests(),))
    result = await RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_patch_bytes=10),
    ).run(request())

    assert result.stop_reason is RepairStopReason.SCOPE_VIOLATION

    git = attempt_git(
        tmp_path,
        attempt_statuses=(status,),
        attempt_diffs=(patch,),
    )
    result = await RepairRuntime(
        boundary,
        git,
        RecordingTests(tmp_path, (failed_tests(),)),
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_patch_bytes=10),
    ).run(request())

    assert result.stop_reason is RepairStopReason.PATCH_LIMIT


@pytest.mark.asyncio
async def test_attempt_test_mutation_is_detected_before_recording_attempt(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    changed = make_status((ordinary(), ordinary()))
    patch = diff("repair")
    git = RecordingGit(
        tmp_path,
        statuses=(clean_status(), clean_status(), status, changed),
        staged_diffs=(
            diff(mode=GitDiffMode.STAGED),
            diff(mode=GitDiffMode.STAGED),
        ),
        unstaged_diffs=(diff(), diff(), patch, diff("mutated")),
    )
    tests = RecordingTests(tmp_path, (failed_tests(), passed_tests()))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.TEST_MUTATED_REPOSITORY
    assert result.attempts == ()


@pytest.mark.asyncio
async def test_attempt_infrastructure_failure_is_recorded_then_stops(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("repair")
    infrastructure = pytest_result(
        status=PytestExecutionStatus.INTERNAL_ERROR,
        report_status=PytestReportStatus.COMPLETE,
        exit_code=3,
    )
    git = attempt_git(
        tmp_path,
        attempt_statuses=(status,),
        attempt_diffs=(patch,),
    )

    result = await RepairRuntime(
        boundary,
        git,
        RecordingTests(tmp_path, (failed_tests(), infrastructure)),
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.TEST_INFRASTRUCTURE_ERROR
    assert len(result.attempts) == 1
    assert result.attempts[0].test.status is PytestExecutionStatus.INTERNAL_ERROR


@pytest.mark.asyncio
async def test_same_failure_fingerprint_stops_after_first_attempt(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("repair")
    git = attempt_git(
        tmp_path,
        attempt_statuses=(status,),
        attempt_diffs=(patch,),
    )

    result = await RepairRuntime(
        boundary,
        git,
        RecordingTests(tmp_path, (failed_tests(), failed_tests())),
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.REPEATED_FAILURE
    assert len(result.attempts) == 1


@pytest.mark.asyncio
async def test_distinct_failures_stop_at_attempt_limit(tmp_path: Path) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch_one = diff("repair one")
    patch_two = diff("repair two")
    git = attempt_git(
        tmp_path,
        attempt_statuses=(status, status),
        attempt_diffs=(patch_one, patch_two),
    )
    tests = RecordingTests(
        tmp_path,
        (
            changed_failure("baseline"),
            changed_failure("failure two"),
            changed_failure("failure three"),
        ),
    )
    worker = RecordingWorker(scope_sha256(("src/app.py",)))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_attempts=2),
    ).run(request())

    assert result.stop_reason is RepairStopReason.MAX_ATTEMPTS
    assert len(result.attempts) == 2
    assert len(worker.calls) == 2


@pytest.mark.asyncio
async def test_repeated_full_diff_stops_second_attempt_without_test(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("same repair")
    git = RecordingGit(
        tmp_path,
        statuses=(clean_status(), clean_status(), status, status, status),
        staged_diffs=(
            diff(mode=GitDiffMode.STAGED),
            diff(mode=GitDiffMode.STAGED),
            diff(mode=GitDiffMode.STAGED),
        ),
        unstaged_diffs=(diff(), diff(), patch, patch, patch),
    )
    tests = RecordingTests(
        tmp_path,
        (changed_failure("baseline"), changed_failure("different")),
    )
    worker = RecordingWorker(scope_sha256(("src/app.py",)))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_attempts=3),
    ).run(request())

    assert result.stop_reason is RepairStopReason.NO_PROGRESS
    assert len(result.attempts) == 1
    assert len(tests.calls) == 2


@pytest.mark.asyncio
async def test_elapsed_budget_stops_before_worker_or_verification(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    git, tests = admitted_dependencies(tmp_path, (failed_tests(),))
    worker = RecordingWorker(scope_sha256(("src/app.py",)))
    before_worker = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_elapsed_seconds=1),
        clock=SequenceClock((0, 0, 2)),
    ).run(request())

    assert before_worker.stop_reason is RepairStopReason.TIME_LIMIT
    assert worker.calls == []

    status = make_status((ordinary(),))
    patch = diff("repair")
    git = RecordingGit(
        tmp_path,
        statuses=(clean_status(), clean_status(), status),
        staged_diffs=(
            diff(mode=GitDiffMode.STAGED),
            diff(mode=GitDiffMode.STAGED),
        ),
        unstaged_diffs=(diff(), diff(), patch),
    )
    tests = RecordingTests(tmp_path, (failed_tests(),))
    worker = RecordingWorker(scope_sha256(("src/app.py",)))
    before_test = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_elapsed_seconds=1),
        clock=SequenceClock((0, 0, 0, 2)),
    ).run(request())

    assert before_test.stop_reason is RepairStopReason.TIME_LIMIT
    assert len(worker.calls) == 1
    assert len(tests.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", (2, 3, 4, 5))
async def test_journal_failure_stops_all_later_repair_work(
    tmp_path: Path,
    fail_at: int,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("repair")
    git = attempt_git(
        tmp_path,
        attempt_statuses=(status,),
        attempt_diffs=(patch,),
    )
    tests = RecordingTests(tmp_path, (failed_tests(), passed_tests()))
    worker = RecordingWorker(scope_sha256(("src/app.py",)))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=FailingAtJournal(fail_at),
    ).run(request())

    assert result.stop_reason is RepairStopReason.PERSISTENCE_ERROR
    assert "secret" not in (result.error or "")
    if fail_at == 2:
        assert worker.calls == []
        assert len(tests.calls) == 1
    if fail_at == 3:
        assert len(tests.calls) == 1
    if fail_at >= 4:
        assert len(tests.calls) == 2


class CancellingWorker(RecordingWorker):
    async def run(self, request: RepairWorkerRequest) -> AgentResult:
        self.calls.append(request)
        raise asyncio.CancelledError


class DirectoryMutatingWorker(RecordingWorker):
    def __init__(self, root: Path, scope_sha256: str) -> None:
        super().__init__(scope_sha256)
        self._root = root

    async def run(self, request: RepairWorkerRequest) -> AgentResult:
        target = self._root / "src" / "app.py"
        target.unlink()
        target.mkdir()
        return await super().run(request)


@pytest.mark.asyncio
async def test_worker_cancellation_propagates(tmp_path: Path) -> None:
    boundary = workspace(tmp_path)
    git, tests = admitted_dependencies(tmp_path, (failed_tests(),))
    worker = CancellingWorker(scope_sha256(("src/app.py",)))
    runtime = RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(request())


@pytest.mark.asyncio
async def test_attempt_revalidates_workspace_file_type_before_test(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("repair")
    git = RecordingGit(
        tmp_path,
        statuses=(clean_status(), clean_status(), status),
        staged_diffs=(
            diff(mode=GitDiffMode.STAGED),
            diff(mode=GitDiffMode.STAGED),
        ),
        unstaged_diffs=(diff(), diff(), patch),
    )
    tests = RecordingTests(tmp_path, (failed_tests(),))
    worker = DirectoryMutatingWorker(
        tmp_path,
        scope_sha256(("src/app.py",)),
    )

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        worker,
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.SCOPE_VIOLATION
    assert len(tests.calls) == 1


@pytest.mark.asyncio
async def test_attempt_revalidates_workspace_file_type_after_test(
    tmp_path: Path,
) -> None:
    boundary = workspace(tmp_path)
    status = make_status((ordinary(),))
    patch = diff("repair")
    git = attempt_git(
        tmp_path,
        attempt_statuses=(status,),
        attempt_diffs=(patch,),
    )
    tests = FileTypeMutatingTests(
        tmp_path,
        (failed_tests(), passed_tests()),
        mutate_on_call=2,
    )

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
    ).run(request())

    assert result.stop_reason is RepairStopReason.TEST_MUTATED_REPOSITORY
    assert result.attempts == ()


@pytest.mark.asyncio
async def test_elapsed_budget_includes_baseline_test(tmp_path: Path) -> None:
    boundary = workspace(tmp_path)
    git, tests = admitted_dependencies(tmp_path, (passed_tests(),))

    result = await RepairRuntime(
        boundary,
        git,
        tests,
        RecordingWorker(scope_sha256(("src/app.py",))),
        StaticRepairApprovalHandler(approved=True),
        journal=RecordingRepairJournal(),
        limits=RepairLimits(max_elapsed_seconds=1),
        clock=SequenceClock((0, 2)),
    ).run(request())

    assert result.stop_reason is RepairStopReason.TIME_LIMIT
