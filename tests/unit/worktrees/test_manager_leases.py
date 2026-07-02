from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import (
    GitIndexPointer,
    WorktreeError,
    WorktreeErrorCode,
    WorktreeLeaseState,
    WorktreeLimits,
)

from .helpers import worktree_profile


class FakeGit:
    def __init__(self, profile_root: Path, *, status: bytes = b"") -> None:
        self.profile_root = profile_root
        self.status = status
        self.added: list[tuple[str, Path, str]] = []
        self.active_paths: list[Path] = []
        self.cleanup_calls: list[tuple[str, Path | None]] = []
        self.fail_remove = False

    async def repository_info(self) -> tuple[Path, bool]:
        return self.profile_root, False

    async def head_sha(self) -> str:
        return "a" * 40

    async def status_porcelain(self) -> bytes:
        return self.status

    async def index_pointers(self) -> tuple[GitIndexPointer, ...]:
        return (
            GitIndexPointer(
                path="src/app.py",
                mode="100644",
                object_id="b" * 40,
            ),
        )

    async def read_blobs(self, object_ids: tuple[str, ...]) -> dict[str, bytes]:
        assert object_ids == ("b" * 40,)
        return {"b" * 40: b"print('ok')\n"}

    async def add_worktree(self, lease_id: str, path: Path, base_sha: str) -> None:
        self.added.append((lease_id, path, base_sha))
        path.mkdir()
        admin = path.parent / "admin"
        admin.mkdir()
        (path / ".git").write_bytes(f"gitdir: {admin}\n".encode())
        self.active_paths.append(path)

    async def unlock_worktree(self, path: Path) -> None:
        self.cleanup_calls.append(("unlock", path))

    async def lock_worktree(self, path: Path, lease_id: str) -> None:
        assert lease_id
        self.cleanup_calls.append(("lock", path))

    async def remove_worktree(self, path: Path) -> None:
        self.cleanup_calls.append(("remove", path))
        if self.fail_remove:
            raise RuntimeError("simulated remove failure")
        shutil.rmtree(path)
        admin = path.parent / "admin"
        shutil.rmtree(admin)
        self.active_paths.remove(path)

    async def prune_worktrees(self) -> None:
        self.cleanup_calls.append(("prune", None))

    async def worktree_paths(self) -> tuple[Path, ...]:
        return tuple(self.active_paths)


@pytest.mark.asyncio
async def test_manager_creates_host_owned_materialized_lease(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    git = FakeGit(profile.repository_root)
    manager = WorktreeManager(
        profile,
        git=git,
        id_factory=lambda: "lease-1",
    )

    lease = await manager.create_lease(child_id="child-1")

    assert lease.lease_id == "lease-1"
    assert lease.child_id == "child-1"
    assert lease.base_sha == "a" * 40
    assert lease.state is WorktreeLeaseState.ACTIVE
    assert lease.worktree_path == profile.state_root / "leases" / "lease-1" / "worktree"
    assert lease.git_admin_dir == profile.state_root / "leases" / "lease-1" / "admin"
    assert (lease.worktree_path / "src" / "app.py").read_bytes() == b"print('ok')\n"
    assert lease.base_manifest.tracked_files == 1
    assert lease.base_manifest.tracked_bytes == 12
    assert git.added == [("lease-1", lease.worktree_path, "a" * 40)]
    assert (profile.state_root / "leases" / "lease-1" / "base-manifest.json").is_file()


@pytest.mark.asyncio
async def test_manager_rejects_dirty_wrong_or_bare_repository_before_add(
    tmp_path: Path,
) -> None:
    profile = worktree_profile(tmp_path)
    dirty = FakeGit(profile.repository_root, status=b"? unsafe.txt\0")
    manager = WorktreeManager(profile, git=dirty, id_factory=lambda: "lease-1")

    with pytest.raises(WorktreeError) as raised:
        await manager.create_lease(child_id="child-1")

    assert raised.value.code is WorktreeErrorCode.REPOSITORY_DIRTY
    assert dirty.added == []


@pytest.mark.asyncio
async def test_manager_enforces_active_lease_limit_before_git_io(tmp_path: Path) -> None:
    profile = worktree_profile(
        tmp_path,
        limits=WorktreeLimits(max_active_leases=1),
    )
    first_git = FakeGit(profile.repository_root)
    first = WorktreeManager(profile, git=first_git, id_factory=lambda: "lease-1")
    await first.create_lease(child_id="child-1")
    second_git = FakeGit(profile.repository_root)
    second = WorktreeManager(profile, git=second_git, id_factory=lambda: "lease-2")

    with pytest.raises(WorktreeError) as raised:
        await second.create_lease(child_id="child-2")

    assert raised.value.code is WorktreeErrorCode.LEASE_LIMIT
    assert second_git.added == []


@pytest.mark.asyncio
async def test_manager_rejects_duplicate_host_identifier_before_git_io(
    tmp_path: Path,
) -> None:
    profile = worktree_profile(tmp_path)
    first = WorktreeManager(
        profile,
        git=FakeGit(profile.repository_root),
        id_factory=lambda: "lease-1",
    )
    await first.create_lease(child_id="child-1")
    duplicate_git = FakeGit(profile.repository_root)
    duplicate = WorktreeManager(
        profile,
        git=duplicate_git,
        id_factory=lambda: "lease-1",
    )

    with pytest.raises(WorktreeError):
        await duplicate.create_lease(child_id="child-2")

    assert duplicate_git.added == []
