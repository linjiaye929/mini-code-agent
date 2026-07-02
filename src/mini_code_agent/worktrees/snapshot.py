from __future__ import annotations

import asyncio
import difflib
import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from mini_code_agent.subagents.models import SubagentStatus
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.models import (
    CandidateDisposition,
    CandidateFile,
    CandidateManifest,
    CandidateOperation,
    CandidateState,
    GitIndexEntry,
    SnapshotOutcome,
    SnapshotStatus,
    WorktreeLease,
    WorktreeLeaseState,
    WorktreeProfile,
)
from mini_code_agent.worktrees.state import WorktreeStateError, WorktreeStateStore


class CandidateBlobReader(Protocol):
    async def read_blobs(self, object_ids: tuple[str, ...]) -> dict[str, bytes]: ...


class SnapshotUnsafeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _ObservedFile:
    path: str
    mode: Literal["100644", "100755"]
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ChangedContent:
    observed: _ObservedFile
    content: bytes
    base: GitIndexEntry | None


@dataclass(frozen=True, slots=True)
class _Scan:
    observed_paths: tuple[str, ...]
    changed_content: tuple[_ChangedContent, ...]
    reasons: tuple[str, ...]


class CandidateSnapshotter:
    def __init__(
        self,
        profile: WorktreeProfile,
        *,
        store: WorktreeStateStore,
        blob_reader: CandidateBlobReader,
    ) -> None:
        self._profile = profile
        self._store = store
        self._blob_reader = blob_reader

    async def snapshot(
        self,
        lease: WorktreeLease,
        ledger: MutationLedger,
        *,
        candidate_id: str,
        child_status: SubagentStatus,
        evidence_sha256: str,
    ) -> SnapshotOutcome:
        if (
            lease.repository_root != self._profile.repository_root
            or lease.state is not WorktreeLeaseState.ACTIVE
            or lease.base_manifest.repository_root != self._profile.repository_root
        ):
            return self._cleanup_required(lease)
        try:
            scan = await asyncio.to_thread(
                _scan_worktree,
                self._profile,
                lease,
                ledger,
            )
            if not scan.observed_paths:
                return SnapshotOutcome(
                    lease_id=lease.lease_id,
                    status=SnapshotStatus.NO_CHANGES,
                )
            files, content, build_reasons = await self._build_candidate_files(lease, scan)
            rejection_reasons = tuple(sorted({*scan.reasons, *build_reasons}))
            disposition = (
                CandidateDisposition.REJECTED if rejection_reasons else CandidateDisposition.READY
            )
            manifest = CandidateManifest.create(
                candidate_id=candidate_id,
                lease_id=lease.lease_id,
                repository_root=lease.repository_root,
                base_sha=lease.base_sha,
                profile_id=self._profile.implementation_profile.profile_id,
                child_id=lease.child_id,
                child_status=child_status,
                evidence_sha256=evidence_sha256,
                disposition=disposition,
                files=files,
                observed_paths=scan.observed_paths,
                rejection_reasons=rejection_reasons,
            )
            await asyncio.to_thread(self._persist_candidate, manifest, content)
        except (SnapshotUnsafeError, WorktreeStateError, OSError, ValueError):
            return self._cleanup_required(lease)
        status = (
            SnapshotStatus.READY
            if disposition is CandidateDisposition.READY
            else SnapshotStatus.REJECTED
        )
        return SnapshotOutcome(
            lease_id=lease.lease_id,
            status=status,
            candidate_id=candidate_id,
            manifest=manifest,
        )

    async def _build_candidate_files(
        self,
        lease: WorktreeLease,
        scan: _Scan,
    ) -> tuple[tuple[CandidateFile, ...], dict[str, bytes], set[str]]:
        changed_base = tuple(item.base for item in scan.changed_content if item.base is not None)
        object_ids = tuple(dict.fromkeys(item.object_id for item in changed_base))
        try:
            base_blobs = await self._blob_reader.read_blobs(object_ids) if object_ids else {}
        except Exception:
            raise SnapshotUnsafeError("Base blobs could not be read.") from None
        files: list[CandidateFile] = []
        content_by_hash: dict[str, bytes] = {}
        reasons: set[str] = set()
        for changed in scan.changed_content:
            after = changed.content
            after_text = _decode_candidate_text(after)
            before_hash: str | None = None
            before_text = ""
            operation = CandidateOperation.ADD
            if changed.base is not None:
                operation = CandidateOperation.MODIFY
                before_hash = changed.base.sha256
                before = base_blobs.get(changed.base.object_id)
                if before is None or hashlib.sha256(before).hexdigest() != before_hash:
                    raise SnapshotUnsafeError("Base blob identity changed.")
                before_text = _decode_candidate_text(before)
                if before_text is None:
                    reasons.add("binary_file" if b"\0" in before else "invalid_utf8")
            diff = ""
            if before_text is not None and after_text is not None:
                diff = _bounded_diff(
                    changed.observed.path,
                    before_text,
                    after_text,
                    self._profile.limits.max_diff_chars,
                )
            digest = changed.observed.sha256
            content_by_hash[digest] = after
            files.append(
                CandidateFile(
                    path=changed.observed.path,
                    operation=operation,
                    mode=changed.observed.mode,
                    before_sha256=before_hash,
                    after_sha256=digest,
                    byte_count=changed.observed.byte_count,
                    line_count=len(after_text.splitlines()) if after_text is not None else 0,
                    diff=diff,
                    content_blob_sha256=digest,
                )
            )
        return (
            tuple(sorted(files, key=lambda item: item.path)),
            content_by_hash,
            reasons,
        )

    def _persist_candidate(
        self,
        manifest: CandidateManifest,
        content_by_hash: dict[str, bytes],
    ) -> None:
        self._store.begin_candidate(manifest.candidate_id)
        for digest in sorted(content_by_hash):
            self._store.write_candidate_blob(
                manifest.candidate_id,
                digest,
                content_by_hash[digest],
            )
        self._store.write_candidate_json(
            manifest.candidate_id,
            "manifest.json",
            manifest.model_dump(mode="json"),
        )
        target = (
            CandidateState.READY
            if manifest.disposition is CandidateDisposition.READY
            else CandidateState.REJECTED
        )
        self._store.transition_candidate(
            manifest.candidate_id,
            CandidateState.BUILDING,
            target,
        )

    def _cleanup_required(self, lease: WorktreeLease) -> SnapshotOutcome:
        with suppress(WorktreeStateError):
            self._store.record_cleanup_required(lease.lease_id, "snapshot_failed")
        return SnapshotOutcome(
            lease_id=lease.lease_id,
            status=SnapshotStatus.CLEANUP_REQUIRED,
        )


def _scan_worktree(
    profile: WorktreeProfile,
    lease: WorktreeLease,
    ledger: MutationLedger,
) -> _Scan:
    records = _walk_regular_files(profile, lease)
    base_by_path = {entry.path: entry for entry in lease.base_manifest.entries}
    record_by_path = {entry.path: entry for entry in records}
    observed_paths: set[str] = set()
    changed_content: list[_ChangedContent] = []
    reasons: set[str] = set()

    for path, base in base_by_path.items():
        observed = record_by_path.get(path)
        if observed is None:
            observed_paths.add(path)
            reasons.add("deleted_path")
            continue
        if observed.mode != base.mode:
            observed_paths.add(path)
            reasons.add("mode_changed")
        if observed.sha256 != base.sha256:
            observed_paths.add(path)
            content = _read_changed_file(
                lease.worktree_path.joinpath(*path.split("/")),
                observed,
                profile,
            )
            changed_content.append(_ChangedContent(observed, content, base))

    for path, observed in record_by_path.items():
        if path in base_by_path:
            continue
        observed_paths.add(path)
        content = _read_changed_file(
            lease.worktree_path.joinpath(*path.split("/")),
            observed,
            profile,
        )
        changed_content.append(_ChangedContent(observed, content, None))

    ordered_paths = tuple(sorted(observed_paths))
    if len(ordered_paths) > profile.limits.max_candidate_files:
        raise SnapshotUnsafeError("Candidate changed-file budget exceeded.")
    if sum(item.observed.byte_count for item in changed_content) > (
        profile.limits.max_candidate_after_bytes
    ):
        raise SnapshotUnsafeError("Candidate after-content budget exceeded.")
    for path in ordered_paths:
        if not _is_allowed(path, profile.allowed_path_prefixes):
            reasons.add("outside_allowed_prefix")
    for item in changed_content:
        if _decode_candidate_text(item.content) is None:
            reasons.add("binary_file" if b"\0" in item.content else "invalid_utf8")
    reasons.update(_validate_ledger(ledger, ordered_paths, base_by_path, record_by_path))
    return _Scan(
        observed_paths=ordered_paths,
        changed_content=tuple(sorted(changed_content, key=lambda item: item.observed.path)),
        reasons=tuple(sorted(reasons)),
    )


def verify_lease_base_clean(
    profile: WorktreeProfile,
    lease: WorktreeLease,
) -> bool:
    try:
        scan = _scan_worktree(profile, lease, MutationLedger(max_entries=1))
    except SnapshotUnsafeError:
        return False
    return not scan.observed_paths


def _walk_regular_files(
    profile: WorktreeProfile,
    lease: WorktreeLease,
) -> tuple[_ObservedFile, ...]:
    root = lease.worktree_path
    if _is_link_or_reparse(root) or not root.is_dir():
        raise SnapshotUnsafeError("Worktree root is unsafe.")
    records: list[_ObservedFile] = []
    identities: set[str] = set()
    base_by_path = {entry.path: entry for entry in lease.base_manifest.entries}
    total_bytes = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        if _is_link_or_reparse(directory):
            raise SnapshotUnsafeError("Worktree directory is linked.")
        try:
            children = tuple(directory.iterdir())
        except OSError:
            raise SnapshotUnsafeError("Worktree directory could not be scanned.") from None
        for child in children:
            relative = child.relative_to(root).as_posix()
            if len(relative) > profile.limits.max_path_chars:
                raise SnapshotUnsafeError("Worktree path budget exceeded.")
            if relative == ".git":
                if _is_link_or_reparse(child) or not child.is_file():
                    raise SnapshotUnsafeError("Worktree administrative file is unsafe.")
                continue
            if any(part.casefold() == ".git" for part in relative.split("/")):
                raise SnapshotUnsafeError("Worktree contains nested Git administration.")
            if _is_link_or_reparse(child):
                raise SnapshotUnsafeError("Worktree contains a link.")
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError:
                raise SnapshotUnsafeError("Worktree entry could not be inspected.") from None
            if stat.S_ISDIR(metadata.st_mode):
                if len(relative.split("/")) > profile.limits.max_tracked_depth:
                    raise SnapshotUnsafeError("Worktree depth budget exceeded.")
                stack.append(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise SnapshotUnsafeError("Worktree contains a special file.")
            identity = relative.casefold()
            if identity in identities:
                raise SnapshotUnsafeError("Worktree paths collide.")
            identities.add(identity)
            total_bytes += metadata.st_size
            if (
                len(records) + 1
                > profile.limits.max_tracked_files + profile.limits.max_candidate_files
                or total_bytes
                > profile.limits.max_tracked_bytes + profile.limits.max_candidate_after_bytes
            ):
                raise SnapshotUnsafeError("Worktree scan budget exceeded.")
            base = base_by_path.get(relative)
            records.append(
                _ObservedFile(
                    path=relative,
                    mode=_observed_mode(metadata.st_mode, base),
                    byte_count=metadata.st_size,
                    sha256=_hash_regular_file(child, metadata.st_size),
                )
            )
    return tuple(sorted(records, key=lambda item: item.path))


def _hash_regular_file(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SnapshotUnsafeError("Worktree file changed type.")
            while chunk := stream.read(64 * 1024):
                count += len(chunk)
                digest.update(chunk)
    except SnapshotUnsafeError:
        raise
    except OSError:
        raise SnapshotUnsafeError("Worktree file could not be hashed.") from None
    if count != expected_size or _is_link_or_reparse(path):
        raise SnapshotUnsafeError("Worktree file changed during snapshot.")
    return digest.hexdigest()


def _read_changed_file(
    path: Path,
    observed: _ObservedFile,
    profile: WorktreeProfile,
) -> bytes:
    if observed.byte_count > profile.limits.max_file_bytes:
        raise SnapshotUnsafeError("Candidate file budget exceeded.")
    try:
        content = path.read_bytes()
    except OSError:
        raise SnapshotUnsafeError("Candidate file could not be read.") from None
    if (
        len(content) != observed.byte_count
        or hashlib.sha256(content).hexdigest() != observed.sha256
        or _is_link_or_reparse(path)
    ):
        raise SnapshotUnsafeError("Candidate file changed during snapshot.")
    return content


def _validate_ledger(
    ledger: MutationLedger,
    observed_paths: tuple[str, ...],
    base_by_path: dict[str, GitIndexEntry],
    record_by_path: dict[str, _ObservedFile],
) -> set[str]:
    if ledger.compromised:
        return {"ledger_compromised"}
    entries = ledger.entries
    if tuple(entry.ordinal for entry in entries) != tuple(range(len(entries))):
        return {"ledger_mismatch"}
    ledger_paths = {entry.path for entry in entries}
    if ledger_paths != set(observed_paths):
        return {"ledger_mismatch"}
    previous_by_path: dict[str, str] = {}
    for entry in entries:
        expected_before = previous_by_path.get(entry.path)
        if expected_before is None:
            base = base_by_path.get(entry.path)
            expected_before = base.sha256 if base is not None else None
        if entry.before_sha256 != expected_before:
            return {"ledger_mismatch"}
        previous_by_path[entry.path] = entry.after_sha256
    for path, final_hash in previous_by_path.items():
        observed = record_by_path.get(path)
        if observed is None or observed.sha256 != final_hash:
            return {"ledger_mismatch"}
    return set()


def _decode_candidate_text(content: bytes) -> str | None:
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _bounded_diff(path: str, before: str, after: str, limit: int) -> str:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if len(diff) <= limit:
        return diff
    marker = "\n... diff truncated by mini-code-agent ...\n"
    if limit <= len(marker):
        return marker[:limit]
    return diff[: max(0, limit - len(marker))] + marker


def _is_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _observed_mode(
    raw_mode: int,
    base: GitIndexEntry | None,
) -> Literal["100644", "100755"]:
    if os.name == "nt":
        return base.mode if base is not None else "100644"
    return "100755" if raw_mode & stat.S_IXUSR else "100644"


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)
