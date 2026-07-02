from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from typing import Any, Protocol

from mini_code_agent.subagents.models import SubagentStatus
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.models import (
    CleanupResult,
    CleanupStatus,
    SnapshotOutcome,
    SnapshotStatus,
    WorktreeFinalizationResult,
    WorktreeLease,
)


class LeaseSnapshotter(Protocol):
    async def snapshot(
        self,
        lease: WorktreeLease,
        ledger: MutationLedger,
        *,
        candidate_id: str,
        child_status: SubagentStatus,
        evidence_sha256: str,
    ) -> SnapshotOutcome: ...


class LeaseCleaner(Protocol):
    async def cleanup_lease(
        self,
        lease: WorktreeLease,
        outcome: SnapshotOutcome,
    ) -> CleanupResult: ...


class WorktreeFinalizer:
    def __init__(
        self,
        *,
        snapshotter: LeaseSnapshotter,
        cleaner: LeaseCleaner,
    ) -> None:
        self._snapshotter = snapshotter
        self._cleaner = cleaner

    async def finalize(
        self,
        lease: WorktreeLease,
        ledger: MutationLedger,
        *,
        candidate_id: str,
        child_status: SubagentStatus,
        evidence_sha256: str,
    ) -> WorktreeFinalizationResult:
        snapshot = await self._snapshotter.snapshot(
            lease,
            ledger,
            candidate_id=candidate_id,
            child_status=child_status,
            evidence_sha256=evidence_sha256,
        )
        cleanup = (
            CleanupResult(
                lease_id=lease.lease_id,
                status=CleanupStatus.CLEANUP_REQUIRED,
            )
            if snapshot.status is SnapshotStatus.CLEANUP_REQUIRED
            else await self._cleaner.cleanup_lease(lease, snapshot)
        )
        return WorktreeFinalizationResult(
            lease_id=lease.lease_id,
            snapshot=snapshot,
            cleanup=cleanup,
        )


async def await_with_cancellation_finalization[T](
    child: Awaitable[T],
    *,
    finalize: Callable[[], Coroutine[Any, Any, object]],
    timeout_seconds: float,
    on_timeout: Callable[[], None] | None = None,
) -> T:
    if not 0 < timeout_seconds <= 300:
        raise ValueError("Cancellation finalization timeout is invalid.")
    try:
        return await child
    except asyncio.CancelledError:
        await run_cancellation_finalization(
            finalize=finalize,
            timeout_seconds=timeout_seconds,
            on_timeout=on_timeout,
        )
        raise


async def run_cancellation_finalization(
    *,
    finalize: Callable[[], Coroutine[Any, Any, object]],
    timeout_seconds: float,
    on_timeout: Callable[[], None] | None = None,
) -> None:
    if not 0 < timeout_seconds <= 300:
        raise ValueError("Cancellation finalization timeout is invalid.")
    task = asyncio.create_task(finalize())
    with suppress(Exception):
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if on_timeout is not None:
                with suppress(Exception):
                    on_timeout()
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
