from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.subagents.models import SubagentStatus
from mini_code_agent.workspace.models import MutationResult
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import (
    CleanupStatus,
    SnapshotOutcome,
    SnapshotStatus,
)
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore

from .helpers import worktree_profile
from .test_manager_leases import FakeGit


async def managed_lease(tmp_path: Path):
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
    return profile, store, git, manager, lease


def no_changes(lease_id: str) -> SnapshotOutcome:
    return SnapshotOutcome(
        lease_id=lease_id,
        status=SnapshotStatus.NO_CHANGES,
    )


@pytest.mark.asyncio
async def test_cleanup_verifies_and_removes_exact_clean_lease(tmp_path: Path) -> None:
    _, _, git, manager, lease = await managed_lease(tmp_path)

    result = await manager.cleanup_lease(lease, no_changes(lease.lease_id))

    assert result.status is CleanupStatus.REMOVED
    assert git.cleanup_calls == [
        ("unlock", lease.worktree_path),
        ("remove", lease.worktree_path),
        ("prune", None),
    ]
    assert not lease.container_path.exists()


@pytest.mark.asyncio
async def test_cleanup_refuses_dirty_unsnapshotted_tree(tmp_path: Path) -> None:
    _, _, git, manager, lease = await managed_lease(tmp_path)
    (lease.worktree_path / "src" / "app.py").write_text("dirty\n", encoding="utf-8")

    result = await manager.cleanup_lease(lease, no_changes(lease.lease_id))

    assert result.status is CleanupStatus.CLEANUP_REQUIRED
    assert git.cleanup_calls == []
    assert lease.worktree_path.exists()
    diagnostic = lease.container_path / "cleanup-required.json"
    assert json.loads(diagnostic.read_text(encoding="utf-8")) == {
        "lease_id": "lease-1",
        "stage": "cleanup_failed",
        "status": "cleanup_required",
    }


@pytest.mark.asyncio
async def test_cleanup_refuses_swapped_admin_identity(tmp_path: Path) -> None:
    _, _, git, manager, lease = await managed_lease(tmp_path)
    replacement = lease.container_path / "replacement-admin"
    replacement.mkdir()
    (lease.worktree_path / ".git").write_bytes(f"gitdir: {replacement}\n".encode())

    result = await manager.cleanup_lease(lease, no_changes(lease.lease_id))

    assert result.status is CleanupStatus.CLEANUP_REQUIRED
    assert git.cleanup_calls == []


@pytest.mark.asyncio
async def test_cleanup_relocks_lease_when_force_remove_fails(tmp_path: Path) -> None:
    _, _, git, manager, lease = await managed_lease(tmp_path)
    git.fail_remove = True

    result = await manager.cleanup_lease(lease, no_changes(lease.lease_id))

    assert result.status is CleanupStatus.CLEANUP_REQUIRED
    assert git.cleanup_calls == [
        ("unlock", lease.worktree_path),
        ("remove", lease.worktree_path),
        ("lock", lease.worktree_path),
    ]
    assert lease.worktree_path.exists()


@pytest.mark.asyncio
async def test_cleanup_accepts_verified_ready_candidate_and_preserves_it(
    tmp_path: Path,
) -> None:
    profile, store, git, manager, lease = await managed_lease(tmp_path)
    target = lease.worktree_path / "src" / "app.py"
    before = target.read_bytes()
    after = b"print('candidate')\n"
    target.write_bytes(after)
    mutation = MutationResult(
        path="src/app.py",
        created=False,
        before_sha256=hashlib.sha256(before).hexdigest(),
        after_sha256=hashlib.sha256(after).hexdigest(),
        byte_count=len(after),
        line_count=1,
        diff="bounded",
    )
    ledger = MutationLedger(max_entries=8)
    call = ToolCall(id="write-1", name="write_file", arguments={})
    ledger.record(
        call,
        ToolResult(
            tool_call_id=call.id,
            content=json.dumps(mutation.model_dump(mode="json")),
        ),
    )
    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=git,
    ).snapshot(
        lease,
        ledger,
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )
    assert outcome.status is SnapshotStatus.READY

    result = await manager.cleanup_lease(lease, outcome)

    assert result.status is CleanupStatus.REMOVED
    assert (profile.state_root / "candidates" / "ready" / "candidate-1").is_dir()


@pytest.mark.asyncio
async def test_cleanup_refuses_tampered_candidate_blob(tmp_path: Path) -> None:
    profile, store, git, manager, lease = await managed_lease(tmp_path)
    target = lease.worktree_path / "src" / "app.py"
    before = target.read_bytes()
    after = b"print('candidate')\n"
    target.write_bytes(after)
    mutation = MutationResult(
        path="src/app.py",
        created=False,
        before_sha256=hashlib.sha256(before).hexdigest(),
        after_sha256=hashlib.sha256(after).hexdigest(),
        byte_count=len(after),
        line_count=1,
        diff="bounded",
    )
    ledger = MutationLedger(max_entries=8)
    call = ToolCall(id="write-1", name="write_file", arguments={})
    ledger.record(
        call,
        ToolResult(
            tool_call_id=call.id,
            content=json.dumps(mutation.model_dump(mode="json")),
        ),
    )
    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=git,
    ).snapshot(
        lease,
        ledger,
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )
    blob = (
        profile.state_root
        / "candidates"
        / "ready"
        / "candidate-1"
        / "blobs"
        / hashlib.sha256(after).hexdigest()
    )
    blob.write_bytes(b"tampered")

    result = await manager.cleanup_lease(lease, outcome)

    assert result.status is CleanupStatus.CLEANUP_REQUIRED
    assert git.cleanup_calls == []
