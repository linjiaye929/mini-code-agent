from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.subagents.models import SubagentStatus
from mini_code_agent.workspace.models import MutationResult
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.models import (
    BaseManifest,
    CandidateDisposition,
    GitIndexEntry,
    MutationLedgerEntry,
    SnapshotStatus,
    WorktreeLease,
    WorktreeLeaseState,
    WorktreeLimits,
    WorktreeProfile,
)
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore

from .helpers import worktree_profile


class BlobReader:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.requests: list[tuple[str, ...]] = []

    async def read_blobs(self, object_ids: tuple[str, ...]) -> dict[str, bytes]:
        self.requests.append(object_ids)
        return {object_id: self.blobs[object_id] for object_id in object_ids}


def lease_for(
    tmp_path: Path,
    *,
    files: dict[str, bytes] | None = None,
    limits: WorktreeLimits | None = None,
) -> tuple[WorktreeProfile, WorktreeLease, WorktreeStateStore, dict[str, bytes]]:
    profile = worktree_profile(tmp_path, limits=limits)
    store = WorktreeStateStore(profile)
    store.initialize()
    paths = store.begin_lease("lease-1")
    paths.worktree.mkdir()
    (paths.worktree / ".git").write_text("gitdir: admin\n", encoding="utf-8")
    base_files = files or {"src/app.py": b"print('base')\n"}
    blobs: dict[str, bytes] = {}
    entries: list[GitIndexEntry] = []
    for index, (path, content) in enumerate(sorted(base_files.items())):
        target = paths.worktree.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        object_id = f"{index + 1:040x}"
        blobs[object_id] = content
        entries.append(
            GitIndexEntry(
                path=path,
                mode="100644",
                object_id=object_id,
                byte_count=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    manifest = BaseManifest.from_entries(
        repository_root=profile.repository_root,
        base_sha="a" * 40,
        entries=tuple(entries),
    )
    lease = WorktreeLease(
        lease_id="lease-1",
        child_id="child-1",
        repository_root=profile.repository_root,
        container_path=paths.container,
        worktree_path=paths.worktree,
        base_sha="a" * 40,
        base_manifest=manifest,
        state=WorktreeLeaseState.ACTIVE,
    )
    return profile, lease, store, blobs


def ledger_for(*entries: MutationLedgerEntry) -> MutationLedger:
    ledger = MutationLedger(max_entries=8)
    for item in entries:
        mutation = MutationResult(
            path=item.path,
            created=item.created,
            before_sha256=item.before_sha256,
            after_sha256=item.after_sha256,
            byte_count=item.byte_count,
            line_count=item.line_count,
            diff="bounded",
        )
        ledger.record(
            ToolCall(id=item.tool_call_id, name=item.tool_name, arguments={}),
            ToolResult(
                tool_call_id=item.tool_call_id,
                content=json.dumps(mutation.model_dump(mode="json")),
            ),
        )
    return ledger


def entry(
    ordinal: int,
    path: str,
    *,
    before: str | None,
    after: str,
    created: bool,
) -> MutationLedgerEntry:
    return MutationLedgerEntry(
        ordinal=ordinal,
        tool_call_id=f"call-{ordinal}",
        tool_name="write_file",
        path=path,
        created=created,
        before_sha256=before,
        after_sha256=after,
        byte_count=12,
        line_count=1,
    )


@pytest.mark.asyncio
async def test_snapshot_persists_ready_candidate_from_exact_scan_and_ledger(
    tmp_path: Path,
) -> None:
    profile, lease, store, blobs = lease_for(tmp_path)
    before = lease.base_manifest.entries[0].sha256
    modified = b"print('changed')\n"
    added = b"NEW = True\n"
    (lease.worktree_path / "src" / "app.py").write_bytes(modified)
    (lease.worktree_path / "src" / "new.py").write_bytes(added)
    ledger = ledger_for(
        entry(
            0,
            "src/app.py",
            before=before,
            after=hashlib.sha256(modified).hexdigest(),
            created=False,
        ),
        entry(
            1,
            "src/new.py",
            before=None,
            after=hashlib.sha256(added).hexdigest(),
            created=True,
        ),
    )
    snapshotter = CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    )

    outcome = await snapshotter.snapshot(
        lease,
        ledger,
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.READY
    assert outcome.manifest is not None
    assert outcome.manifest.disposition is CandidateDisposition.READY
    assert [file.path for file in outcome.manifest.files] == [
        "src/app.py",
        "src/new.py",
    ]
    assert outcome.manifest.changed_files == 2
    ready = profile.state_root / "candidates" / "ready" / "candidate-1"
    persisted = json.loads((ready / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["manifest_sha256"] == outcome.manifest.manifest_sha256
    for file in outcome.manifest.files:
        assert (ready / "blobs" / file.content_blob_sha256).read_bytes() in {
            modified,
            added,
        }


@pytest.mark.asyncio
async def test_snapshot_returns_no_candidate_for_unchanged_tree(tmp_path: Path) -> None:
    profile, lease, store, blobs = lease_for(tmp_path)
    snapshotter = CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    )

    outcome = await snapshotter.snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.NO_CHANGES
    assert outcome.candidate_id is None
    assert not (profile.state_root / "candidates" / "ready" / "candidate-1").exists()


@pytest.mark.asyncio
async def test_snapshot_persists_extra_regular_mutation_as_rejected_forensics(
    tmp_path: Path,
) -> None:
    profile, lease, store, blobs = lease_for(tmp_path)
    extra = b"not in ledger\n"
    (lease.worktree_path / "src" / "extra.py").write_bytes(extra)
    snapshotter = CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    )

    outcome = await snapshotter.snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.FAILED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.REJECTED
    assert outcome.manifest is not None
    assert outcome.manifest.disposition is CandidateDisposition.REJECTED
    assert "ledger_mismatch" in outcome.manifest.rejection_reasons
    rejected = profile.state_root / "candidates" / "rejected" / "candidate-1"
    assert (rejected / "blobs" / hashlib.sha256(extra).hexdigest()).read_bytes() == extra


@pytest.mark.asyncio
async def test_snapshot_rejects_deletion_binary_and_out_of_scope_changes(
    tmp_path: Path,
) -> None:
    profile, lease, store, blobs = lease_for(
        tmp_path,
        files={
            "src/app.py": b"base\n",
            "docs/guide.md": b"guide\n",
        },
    )
    (lease.worktree_path / "src" / "app.py").unlink()
    (lease.worktree_path / "docs" / "guide.md").write_bytes(b"\x00binary")
    snapshotter = CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    )

    outcome = await snapshotter.snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.REJECTED
    assert outcome.manifest is not None
    assert {"deleted_path", "binary_file", "outside_allowed_prefix"} <= set(
        outcome.manifest.rejection_reasons
    )


@pytest.mark.asyncio
async def test_snapshot_retains_unsafe_linked_tree_for_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, lease, store, blobs = lease_for(tmp_path)

    def marks_app_as_link(path: Path) -> bool:
        return path.name == "app.py"

    monkeypatch.setattr(
        "mini_code_agent.worktrees.snapshot._is_link_or_reparse",
        marks_app_as_link,
    )
    snapshotter = CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    )

    outcome = await snapshotter.snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.CLEANUP_REQUIRED
    assert outcome.candidate_id is None
    assert lease.worktree_path.exists()


@pytest.mark.asyncio
async def test_snapshot_retains_candidate_that_exceeds_after_content_budget(
    tmp_path: Path,
) -> None:
    profile, lease, store, blobs = lease_for(
        tmp_path,
        limits=WorktreeLimits(
            max_file_bytes=4,
            max_candidate_after_bytes=4,
        ),
    )
    (lease.worktree_path / "src" / "app.py").write_bytes(b"12345")
    snapshotter = CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    )

    outcome = await snapshotter.snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.CLEANUP_REQUIRED
    assert not (profile.state_root / "candidates" / "rejected" / "candidate-1").exists()


@pytest.mark.asyncio
async def test_snapshot_diff_never_exceeds_profile_limit(tmp_path: Path) -> None:
    profile, lease, store, blobs = lease_for(
        tmp_path,
        limits=WorktreeLimits(max_diff_chars=16),
    )
    modified = b"print('a much longer changed value')\n"
    base = lease.base_manifest.entries[0]
    (lease.worktree_path / "src" / "app.py").write_bytes(modified)
    ledger = ledger_for(
        entry(
            0,
            "src/app.py",
            before=base.sha256,
            after=hashlib.sha256(modified).hexdigest(),
            created=False,
        )
    )

    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    ).snapshot(
        lease,
        ledger,
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.READY
    assert outcome.manifest is not None
    assert len(outcome.manifest.files[0].diff) <= 16


@pytest.mark.asyncio
async def test_snapshot_rejects_mode_change(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows does not expose the Git executable bit.")
    profile, lease, store, blobs = lease_for(tmp_path)
    (lease.worktree_path / "src" / "app.py").chmod(0o755)

    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    ).snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.REJECTED
    assert outcome.manifest is not None
    assert "mode_changed" in outcome.manifest.rejection_reasons


@pytest.mark.asyncio
async def test_snapshot_retains_nested_git_administration(tmp_path: Path) -> None:
    profile, lease, store, blobs = lease_for(tmp_path)
    nested = lease.worktree_path / "src" / ".git"
    nested.mkdir()
    (nested / "config").write_text("unsafe", encoding="utf-8")

    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    ).snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.CLEANUP_REQUIRED


@pytest.mark.asyncio
async def test_snapshot_retains_case_colliding_paths(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows test filesystem is case-insensitive.")
    profile, lease, store, blobs = lease_for(tmp_path)
    (lease.worktree_path / "src" / "APP.py").write_text("collision\n", encoding="utf-8")

    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    ).snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.CLEANUP_REQUIRED


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"),
    reason="FIFO creation is POSIX-only.",
)
async def test_snapshot_retains_special_file(tmp_path: Path) -> None:
    profile, lease, store, blobs = lease_for(tmp_path)
    os.mkfifo(lease.worktree_path / "src" / "pipe")

    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=BlobReader(blobs),
    ).snapshot(
        lease,
        MutationLedger(max_entries=8),
        candidate_id="candidate-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.CLEANUP_REQUIRED
