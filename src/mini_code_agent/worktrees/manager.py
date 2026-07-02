from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from mini_code_agent.worktrees.git import WorktreeGit
from mini_code_agent.worktrees.materialize import MaterializationError, materialize_index
from mini_code_agent.worktrees.models import (
    BaseManifest,
    GitIndexPointer,
    WorktreeError,
    WorktreeErrorCode,
    WorktreeLease,
    WorktreeLeaseState,
    WorktreeProfile,
)
from mini_code_agent.worktrees.state import WorktreeStateError, WorktreeStateStore

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class WorktreeGitService(Protocol):
    async def repository_info(self) -> tuple[Path, bool]: ...

    async def head_sha(self) -> str: ...

    async def status_porcelain(self) -> bytes: ...

    async def index_pointers(self) -> tuple[GitIndexPointer, ...]: ...

    async def read_blobs(self, object_ids: tuple[str, ...]) -> dict[str, bytes]: ...

    async def add_worktree(self, lease_id: str, path: Path, base_sha: str) -> None: ...


class WorktreeManager:
    def __init__(
        self,
        profile: WorktreeProfile,
        *,
        git: WorktreeGitService | None = None,
        store: WorktreeStateStore | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._profile = profile
        self._git = git or WorktreeGit(profile)
        self._store = store or WorktreeStateStore(profile)
        self._id_factory = id_factory or _new_lease_id

    async def create_lease(self, *, child_id: str) -> WorktreeLease:
        if _IDENTIFIER.fullmatch(child_id) is None:
            raise WorktreeError(
                WorktreeErrorCode.INVALID_PROFILE,
                "Implementation child identifier is invalid.",
            )
        try:
            self._store.initialize()
            if len(self._store.active_lease_ids()) >= self._profile.limits.max_active_leases:
                raise WorktreeError(
                    WorktreeErrorCode.LEASE_LIMIT,
                    "Active Worktree lease limit was reached.",
                )
            lease_id = self._id_factory()
            paths = self._store.begin_lease(lease_id)
        except WorktreeError:
            raise
        except (WorktreeStateError, ValueError):
            raise WorktreeError(
                WorktreeErrorCode.WORKTREE_CREATE_FAILED,
                "Worktree lease state could not be created.",
            ) from None

        worktree_created = False
        try:
            base_sha, pointers, blobs = await self._read_clean_base()
            await self._git.add_worktree(lease_id, paths.worktree, base_sha)
            worktree_created = True
            entries = materialize_index(
                paths.worktree,
                pointers,
                blobs,
                limits=self._profile.limits,
            )
            manifest = BaseManifest.from_entries(
                repository_root=self._profile.repository_root,
                base_sha=base_sha,
                entries=entries,
            )
            self._store.write_lease_json(
                lease_id,
                "base-manifest.json",
                manifest.model_dump(mode="json"),
            )
            lease = WorktreeLease(
                lease_id=lease_id,
                child_id=child_id,
                repository_root=self._profile.repository_root,
                container_path=paths.container,
                worktree_path=paths.worktree,
                base_sha=base_sha,
                base_manifest=manifest,
                state=WorktreeLeaseState.ACTIVE,
            )
            self._store.write_lease_json(
                lease_id,
                "lease.json",
                lease.model_dump(mode="json", exclude={"base_manifest"}),
            )
            return lease
        except WorktreeError:
            if not worktree_created:
                self._abandon_empty_lease(lease_id)
            raise
        except (MaterializationError, WorktreeStateError):
            if not worktree_created:
                self._abandon_empty_lease(lease_id)
            raise WorktreeError(
                WorktreeErrorCode.MATERIALIZATION_FAILED,
                "Worktree lease could not be materialized.",
            ) from None
        except Exception:
            if not worktree_created:
                self._abandon_empty_lease(lease_id)
            raise WorktreeError(
                WorktreeErrorCode.WORKTREE_CREATE_FAILED,
                "Worktree lease could not be created.",
            ) from None

    async def _read_clean_base(
        self,
    ) -> tuple[str, tuple[GitIndexPointer, ...], dict[str, bytes]]:
        top_level, bare = await self._git.repository_info()
        if bare or top_level != self._profile.repository_root:
            raise WorktreeError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Pinned repository identity is unsupported.",
            )
        base_sha = await self._git.head_sha()
        if await self._git.status_porcelain():
            raise WorktreeError(
                WorktreeErrorCode.REPOSITORY_DIRTY,
                "Pinned repository must be fully clean.",
            )
        pointers = await self._git.index_pointers()
        object_ids = tuple(dict.fromkeys(pointer.object_id for pointer in pointers))
        blobs = await self._git.read_blobs(object_ids) if object_ids else {}
        if await self._git.head_sha() != base_sha or await self._git.status_porcelain():
            raise WorktreeError(
                WorktreeErrorCode.REPOSITORY_DIRTY,
                "Pinned repository changed during lease creation.",
            )
        return base_sha, pointers, blobs

    def _abandon_empty_lease(self, lease_id: str) -> None:
        with suppress(WorktreeStateError):
            self._store.abandon_empty_lease(lease_id)


def _new_lease_id() -> str:
    return f"lease-{secrets.token_hex(16)}"
