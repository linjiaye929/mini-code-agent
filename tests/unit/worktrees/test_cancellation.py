from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mini_code_agent.subagents.models import SubagentStatus
from mini_code_agent.worktrees.finalization import (
    WorktreeFinalizer,
    await_with_cancellation_finalization,
)
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore

from .helpers import worktree_profile
from .test_manager_leases import FakeGit


@pytest.mark.asyncio
async def test_child_cancellation_waits_for_shielded_finalization_then_reraises() -> None:
    child_started = asyncio.Event()
    finalized = asyncio.Event()

    async def child() -> None:
        child_started.set()
        await asyncio.Event().wait()

    async def finalize() -> None:
        await asyncio.sleep(0.01)
        finalized.set()

    task = asyncio.create_task(
        await_with_cancellation_finalization(
            child(),
            finalize=finalize,
            timeout_seconds=1,
        )
    )
    await child_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert finalized.is_set()


@pytest.mark.asyncio
async def test_cancellation_finalization_timeout_cancels_finalizer_and_reraises() -> None:
    child_started = asyncio.Event()
    finalizer_cancelled = asyncio.Event()
    timeout_recorded = False

    async def child() -> None:
        child_started.set()
        await asyncio.Event().wait()

    async def finalize() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            finalizer_cancelled.set()
            raise

    def record_timeout() -> None:
        nonlocal timeout_recorded
        timeout_recorded = True

    task = asyncio.create_task(
        await_with_cancellation_finalization(
            child(),
            finalize=finalize,
            timeout_seconds=0.05,
            on_timeout=lambda: record_timeout(),
        )
    )
    await child_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert finalizer_cancelled.is_set()
    assert timeout_recorded is True


@pytest.mark.asyncio
async def test_normal_child_completion_does_not_run_cancellation_finalizer() -> None:
    called = False

    async def finalize() -> None:
        nonlocal called
        called = True

    result = await await_with_cancellation_finalization(
        asyncio.sleep(0, result="complete"),
        finalize=finalize,
        timeout_seconds=1,
    )

    assert result == "complete"
    assert called is False


@pytest.mark.asyncio
async def test_cancelled_child_runs_real_snapshot_and_cleanup_before_reraise(
    tmp_path: Path,
) -> None:
    profile = worktree_profile(tmp_path)
    store = WorktreeStateStore(profile)
    git = FakeGit(profile.repository_root)
    manager = WorktreeManager(
        profile,
        git=git,
        store=store,
        id_factory=lambda: "lease-1",
    )
    lease = await manager.create_lease(child_id="child-1")
    finalizer = WorktreeFinalizer(
        snapshotter=CandidateSnapshotter(
            profile,
            store=store,
            blob_reader=git,
        ),
        cleaner=manager,
    )
    child_started = asyncio.Event()

    async def child() -> None:
        child_started.set()
        await asyncio.Event().wait()

    async def finalize() -> object:
        return await finalizer.finalize(
            lease,
            MutationLedger(max_entries=8),
            candidate_id="candidate-1",
            child_status=SubagentStatus.TIMED_OUT,
            evidence_sha256="e" * 64,
        )

    task = asyncio.create_task(
        await_with_cancellation_finalization(
            child(),
            finalize=finalize,
            timeout_seconds=2,
        )
    )
    await child_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not lease.container_path.exists()
