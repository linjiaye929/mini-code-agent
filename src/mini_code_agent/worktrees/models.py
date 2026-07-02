from __future__ import annotations

import hashlib
import json
import os
import stat
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from mini_code_agent.subagents.models import (
    SubagentChildResult,
    SubagentProfile,
    SubagentStatus,
)

_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
_SHA1 = r"^[0-9a-f]{40}$"
_SHA256 = r"^[0-9a-f]{64}$"
_MAX_TRACKED_BYTES = 512 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 8 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024

RelativePath = Annotated[str, Field(min_length=1, max_length=1024)]
Sha256 = Annotated[str, Field(pattern=_SHA256)]


class WorktreeErrorCode(StrEnum):
    INVALID_PROFILE = "invalid_profile"
    REPOSITORY_DIRTY = "repository_dirty"
    REPOSITORY_UNSUPPORTED = "repository_unsupported"
    LEASE_LIMIT = "lease_limit"
    WORKTREE_CREATE_FAILED = "worktree_create_failed"
    MATERIALIZATION_FAILED = "materialization_failed"
    SNAPSHOT_FAILED = "snapshot_failed"
    CLEANUP_REQUIRED = "cleanup_required"
    NO_CANDIDATE_CHANGES = "no_candidate_changes"
    CANDIDATE_CORRUPT = "candidate_corrupt"
    CANDIDATE_STALE = "candidate_stale"
    CANDIDATE_CONFLICT = "candidate_conflict"
    APPLY_FAILED_ROLLED_BACK = "apply_failed_rolled_back"
    APPLY_UNCERTAIN = "apply_uncertain"


class WorktreeError(RuntimeError):
    def __init__(self, code: WorktreeErrorCode, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class WorktreeLeaseState(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    SNAPSHOTTING = "snapshotting"
    CLEANUP_REQUIRED = "cleanup_required"
    REMOVED = "removed"


class CandidateState(StrEnum):
    BUILDING = "building"
    READY = "ready"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    DISCARDING = "discarding"
    UNCERTAIN = "uncertain"


class CandidateOperation(StrEnum):
    ADD = "add"
    MODIFY = "modify"


class CandidateDisposition(StrEnum):
    READY = "ready"
    REJECTED = "rejected"


class SnapshotStatus(StrEnum):
    READY = "ready"
    REJECTED = "rejected"
    NO_CHANGES = "no_changes"
    CLEANUP_REQUIRED = "cleanup_required"


class CleanupStatus(StrEnum):
    REMOVED = "removed"
    CLEANUP_REQUIRED = "cleanup_required"


class AdoptionStatus(StrEnum):
    APPLIED = "applied"
    CONFLICT = "conflict"
    APPLY_FAILED_ROLLED_BACK = "apply_failed_rolled_back"
    APPLY_UNCERTAIN = "apply_uncertain"
    RECOVERED_READY = "recovered_ready"


class DiscardStatus(StrEnum):
    DISCARDED = "discarded"
    CONFLICT = "conflict"


class WorktreeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_active_leases: int = Field(default=2, ge=1, le=4)
    max_tracked_files: int = Field(default=10_000, ge=1, le=20_000)
    max_tracked_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1,
        le=_MAX_TRACKED_BYTES,
    )
    max_tracked_depth: int = Field(default=32, ge=1, le=64)
    max_candidate_files: int = Field(default=32, ge=1, le=128)
    max_candidate_after_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1,
        le=_MAX_CANDIDATE_BYTES,
    )
    max_file_bytes: int = Field(default=1024 * 1024, ge=1, le=_MAX_FILE_BYTES)
    max_path_chars: int = Field(default=1024, ge=1, le=1024)
    max_diff_chars: int = Field(default=32_768, ge=1, le=65_536)
    cleanup_timeout_seconds: float = Field(default=30, gt=0, le=300)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.max_candidate_after_bytes < self.max_file_bytes:
            raise ValueError("Candidate byte limit cannot be lower than the per-file limit.")
        return self


class WorktreeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_root: Path
    state_root: Path
    git_executable: Path
    allowed_path_prefixes: tuple[RelativePath, ...] = Field(min_length=1, max_length=64)
    implementation_profile: SubagentProfile
    limits: WorktreeLimits = Field(default_factory=WorktreeLimits)

    @field_validator("repository_root", "state_root", "git_executable", mode="before")
    @classmethod
    def resolve_host_path(cls, value: object) -> Path:
        path = Path(value)  # type: ignore[arg-type]
        if not path.is_absolute():
            raise ValueError("Worktree host paths must be absolute.")
        try:
            return path.resolve(strict=True)
        except OSError:
            raise ValueError("Worktree host paths must already exist.") from None

    @field_validator("allowed_path_prefixes", mode="before")
    @classmethod
    def normalize_allowed_prefixes(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("Allowed path prefixes must be a sequence.")
        items = cast(list[object] | tuple[object, ...], value)
        normalized = tuple(
            _normalize_relative_path(str(item), allow_trailing_slash=True) for item in items
        )
        if len({item.casefold() for item in normalized}) != len(normalized):
            raise ValueError("Allowed path prefixes must be case-insensitively unique.")
        return normalized

    @model_validator(mode="after")
    def validate_host_configuration(self) -> Self:
        if not self.repository_root.is_dir():
            raise ValueError("Repository root must be an existing directory.")
        if not self.state_root.is_dir():
            raise ValueError("State root must be an existing directory.")
        if not _is_regular_unlinked_file(self.git_executable):
            raise ValueError("Git executable must be an existing unlinked regular file.")
        if _paths_overlap(self.repository_root, self.state_root):
            raise ValueError("State root must be separate from the repository.")
        if _is_link_or_reparse(self.repository_root) or _is_link_or_reparse(self.state_root):
            raise ValueError("Worktree roots cannot be links or reparse points.")
        _validate_secure_state_ancestors(self.state_root)
        if self.implementation_profile.mode != "implementation":
            raise ValueError("Worktree profile requires an implementation Subagent profile.")
        return self


class GitIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: RelativePath
    mode: Literal["100644", "100755"]
    object_id: str = Field(pattern=_SHA1)
    stage: Literal[0] = 0
    byte_count: int = Field(ge=0, le=_MAX_TRACKED_BYTES)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value)


class GitIndexPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: RelativePath
    mode: Literal["100644", "100755"]
    object_id: str = Field(pattern=_SHA1)
    stage: Literal[0] = 0

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value)


class BaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_root: Path
    base_sha: str = Field(pattern=_SHA1)
    entries: tuple[GitIndexEntry, ...] = Field(max_length=20_000)
    tracked_files: int = Field(ge=0, le=20_000)
    tracked_bytes: int = Field(ge=0, le=_MAX_TRACKED_BYTES)
    manifest_sha256: Sha256

    @classmethod
    def from_entries(
        cls,
        *,
        repository_root: Path,
        base_sha: str,
        entries: tuple[GitIndexEntry, ...],
    ) -> Self:
        tracked_files = len(entries)
        tracked_bytes = sum(entry.byte_count for entry in entries)
        projection = _base_manifest_projection(
            repository_root=repository_root,
            base_sha=base_sha,
            entries=entries,
            tracked_files=tracked_files,
            tracked_bytes=tracked_bytes,
        )
        return cls(
            repository_root=repository_root,
            base_sha=base_sha,
            entries=entries,
            tracked_files=tracked_files,
            tracked_bytes=tracked_bytes,
            manifest_sha256=_canonical_sha256(projection),
        )

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if tuple(sorted(self.entries, key=lambda entry: entry.path)) != self.entries:
            raise ValueError("Base manifest entries must be in canonical path order.")
        if len({entry.path.casefold() for entry in self.entries}) != len(self.entries):
            raise ValueError("Base manifest paths must be case-insensitively unique.")
        if self.tracked_files != len(self.entries) or self.tracked_bytes != sum(
            entry.byte_count for entry in self.entries
        ):
            raise ValueError("Base manifest counts are inconsistent.")
        projection = _base_manifest_projection(
            repository_root=self.repository_root,
            base_sha=self.base_sha,
            entries=self.entries,
            tracked_files=self.tracked_files,
            tracked_bytes=self.tracked_bytes,
        )
        if self.manifest_sha256 != _canonical_sha256(projection):
            raise ValueError("Base manifest hash is inconsistent.")
        return self


class WorktreeLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(pattern=_IDENTIFIER)
    child_id: str = Field(pattern=_IDENTIFIER)
    repository_root: Path
    container_path: Path
    worktree_path: Path
    git_admin_dir: Path
    base_sha: str = Field(pattern=_SHA1)
    base_manifest: BaseManifest
    state: WorktreeLeaseState

    @model_validator(mode="after")
    def validate_paths_and_base(self) -> Self:
        if (
            not self.repository_root.is_absolute()
            or not self.container_path.is_absolute()
            or not self.worktree_path.is_absolute()
            or not self.git_admin_dir.is_absolute()
            or self.worktree_path != self.container_path / "worktree"
            or self.container_path.name != self.lease_id
            or self.base_manifest.repository_root != self.repository_root
            or self.base_manifest.base_sha != self.base_sha
        ):
            raise ValueError("Worktree lease identity is inconsistent.")
        return self


class MutationLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=0, le=127)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: Literal["write_file", "edit_file"]
    path: RelativePath
    created: bool
    before_sha256: Sha256 | None = None
    after_sha256: Sha256
    byte_count: int = Field(ge=0, le=_MAX_FILE_BYTES)
    line_count: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value)

    @field_validator("tool_call_id")
    @classmethod
    def reject_nul_call_id(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("Mutation ToolCall identifier cannot contain NUL.")
        return value

    @model_validator(mode="after")
    def validate_hashes(self) -> Self:
        if self.created != (self.before_sha256 is None):
            raise ValueError("Created mutation and before hash are inconsistent.")
        if self.before_sha256 == self.after_sha256:
            raise ValueError("Mutation must change the content hash.")
        return self


class CandidateFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: RelativePath
    operation: CandidateOperation
    mode: Literal["100644", "100755"]
    before_sha256: Sha256 | None = None
    after_sha256: Sha256
    byte_count: int = Field(ge=0, le=_MAX_FILE_BYTES)
    line_count: int = Field(ge=0)
    diff: str = Field(max_length=65_536)
    content_blob_sha256: Sha256

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value)

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        if (self.operation is CandidateOperation.ADD) != (self.before_sha256 is None):
            raise ValueError("Candidate operation and before hash are inconsistent.")
        if self.before_sha256 == self.after_sha256:
            raise ValueError("Candidate must change the content hash.")
        if self.content_blob_sha256 != self.after_sha256:
            raise ValueError("Candidate content blob must match the after hash.")
        return self


class CandidateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=_IDENTIFIER)
    lease_id: str = Field(pattern=_IDENTIFIER)
    repository_root: Path
    base_sha: str = Field(pattern=_SHA1)
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    child_id: str = Field(pattern=_IDENTIFIER)
    child_status: SubagentStatus
    evidence_sha256: Sha256
    disposition: CandidateDisposition
    files: tuple[CandidateFile, ...] = Field(max_length=128)
    observed_paths: tuple[RelativePath, ...] = Field(max_length=128)
    changed_files: int = Field(ge=0, le=128)
    after_content_bytes: int = Field(ge=0, le=_MAX_CANDIDATE_BYTES)
    rejection_reasons: tuple[str, ...] = Field(default=(), max_length=32)
    manifest_sha256: Sha256

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        lease_id: str,
        repository_root: Path,
        base_sha: str,
        profile_id: str,
        child_id: str,
        child_status: SubagentStatus,
        evidence_sha256: str,
        disposition: CandidateDisposition,
        files: tuple[CandidateFile, ...],
        observed_paths: tuple[str, ...],
        rejection_reasons: tuple[str, ...] = (),
    ) -> Self:
        changed_files = len(observed_paths)
        after_content_bytes = sum(item.byte_count for item in files)
        projection = _candidate_manifest_projection(
            candidate_id=candidate_id,
            lease_id=lease_id,
            repository_root=repository_root,
            base_sha=base_sha,
            profile_id=profile_id,
            child_id=child_id,
            child_status=child_status,
            evidence_sha256=evidence_sha256,
            disposition=disposition,
            files=files,
            observed_paths=observed_paths,
            changed_files=changed_files,
            after_content_bytes=after_content_bytes,
            rejection_reasons=rejection_reasons,
        )
        return cls(
            candidate_id=candidate_id,
            lease_id=lease_id,
            repository_root=repository_root,
            base_sha=base_sha,
            profile_id=profile_id,
            child_id=child_id,
            child_status=child_status,
            evidence_sha256=evidence_sha256,
            disposition=disposition,
            files=files,
            observed_paths=observed_paths,
            changed_files=changed_files,
            after_content_bytes=after_content_bytes,
            rejection_reasons=rejection_reasons,
            manifest_sha256=_canonical_sha256(projection),
        )

    @field_validator("observed_paths")
    @classmethod
    def validate_observed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_normalize_relative_path(path) for path in value)

    @field_validator("rejection_reasons")
    @classmethod
    def validate_rejection_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not reason
            or len(reason) > 64
            or not reason.replace("_", "").isalnum()
            or reason.lower() != reason
            for reason in value
        ):
            raise ValueError("Candidate rejection reasons are invalid.")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        file_paths = tuple(item.path for item in self.files)
        if (
            tuple(sorted(file_paths)) != file_paths
            or len({path.casefold() for path in file_paths}) != len(file_paths)
            or tuple(sorted(self.observed_paths)) != self.observed_paths
            or len({path.casefold() for path in self.observed_paths}) != len(self.observed_paths)
            or self.changed_files != len(self.observed_paths)
            or self.after_content_bytes != sum(item.byte_count for item in self.files)
        ):
            raise ValueError("Candidate manifest paths or counts are inconsistent.")
        if self.disposition is CandidateDisposition.READY:
            if self.rejection_reasons or not self.files or self.observed_paths != file_paths:
                raise ValueError("Ready candidate manifest is inconsistent.")
        elif not self.rejection_reasons:
            raise ValueError("Rejected candidate manifest requires a reason.")
        projection = _candidate_manifest_projection(
            candidate_id=self.candidate_id,
            lease_id=self.lease_id,
            repository_root=self.repository_root,
            base_sha=self.base_sha,
            profile_id=self.profile_id,
            child_id=self.child_id,
            child_status=self.child_status,
            evidence_sha256=self.evidence_sha256,
            disposition=self.disposition,
            files=self.files,
            observed_paths=self.observed_paths,
            changed_files=self.changed_files,
            after_content_bytes=self.after_content_bytes,
            rejection_reasons=self.rejection_reasons,
        )
        if self.manifest_sha256 != _canonical_sha256(projection):
            raise ValueError("Candidate manifest hash is inconsistent.")
        return self


class SnapshotOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(pattern=_IDENTIFIER)
    status: SnapshotStatus
    candidate_id: str | None = Field(default=None, pattern=_IDENTIFIER)
    manifest: CandidateManifest | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in {SnapshotStatus.READY, SnapshotStatus.REJECTED}:
            expected = CandidateDisposition(self.status.value)
            if (
                self.candidate_id is None
                or self.manifest is None
                or self.manifest.candidate_id != self.candidate_id
                or self.manifest.lease_id != self.lease_id
                or self.manifest.disposition is not expected
            ):
                raise ValueError("Snapshot candidate outcome is inconsistent.")
        elif self.candidate_id is not None or self.manifest is not None:
            raise ValueError("Snapshot non-candidate outcome is inconsistent.")
        return self


class CleanupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(pattern=_IDENTIFIER)
    status: CleanupStatus


class AdoptionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=_IDENTIFIER)
    status: AdoptionStatus
    changed_files: int = Field(ge=0, le=128)
    manifest_sha256: Sha256


class DiscardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=_IDENTIFIER)
    status: DiscardStatus
    changed_files: int = Field(ge=0, le=128)
    manifest_sha256: Sha256


class WorktreeFinalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(pattern=_IDENTIFIER)
    snapshot: SnapshotOutcome
    cleanup: CleanupResult

    @model_validator(mode="after")
    def validate_lease_identity(self) -> Self:
        if self.snapshot.lease_id != self.lease_id or self.cleanup.lease_id != self.lease_id:
            raise ValueError("Worktree finalization lease identity is inconsistent.")
        return self


class ImplementationRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    child: SubagentChildResult
    finalization: WorktreeFinalizationResult
    duration_ms: int = Field(ge=0, le=3_700_000)
    result_sha256: Sha256

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        child: SubagentChildResult,
        finalization: WorktreeFinalizationResult,
        duration_ms: int,
    ) -> Self:
        projection = _implementation_run_projection(
            profile_id=profile_id,
            child=child,
            finalization=finalization,
            duration_ms=duration_ms,
        )
        return cls(
            profile_id=profile_id,
            child=child,
            finalization=finalization,
            duration_ms=duration_ms,
            result_sha256=_canonical_sha256(projection),
        )

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        manifest = self.finalization.snapshot.manifest
        if self.child.profile_id != self.profile_id or (
            manifest is not None
            and (
                manifest.profile_id != self.profile_id
                or manifest.child_id != self.child.child_id
                or manifest.child_status is not self.child.status
                or manifest.evidence_sha256 != self.child.result_sha256
            )
        ):
            raise ValueError("Implementation run identity is inconsistent.")
        projection = _implementation_run_projection(
            profile_id=self.profile_id,
            child=self.child,
            finalization=self.finalization,
            duration_ms=self.duration_ms,
        )
        if self.result_sha256 != _canonical_sha256(projection):
            raise ValueError("Implementation run hash is inconsistent.")
        return self


def _normalize_relative_path(value: str, *, allow_trailing_slash: bool = False) -> str:
    if "\0" in value or "\\" in value:
        raise ValueError("Worktree paths must be NUL-free POSIX paths.")
    normalized = value[:-1] if allow_trailing_slash and value.endswith("/") else value
    if not normalized or normalized.startswith("/") or "//" in normalized:
        raise ValueError("Worktree paths must be canonical relative paths.")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Worktree paths must not contain traversal segments.")
    if any(part.casefold() == ".git" for part in path.parts):
        raise ValueError("Worktree paths cannot traverse .git.")
    if path.as_posix() != normalized:
        raise ValueError("Worktree paths must already be normalized.")
    return normalized


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _is_regular_unlinked_file(path: Path) -> bool:
    if _is_link_or_reparse(path):
        return False
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except OSError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validate_secure_state_ancestors(state_root: Path) -> None:
    for path in (state_root, *state_root.parents):
        if _is_link_or_reparse(path) or not path.is_dir():
            raise ValueError("State root ancestors must be unlinked directories.")
    if os.name != "nt":
        mode = state_root.stat(follow_symlinks=False).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("State root must exclude group and other access.")


def _base_manifest_projection(
    *,
    repository_root: Path,
    base_sha: str,
    entries: tuple[GitIndexEntry, ...],
    tracked_files: int,
    tracked_bytes: int,
) -> dict[str, object]:
    return {
        "base_sha": base_sha,
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "repository_root": str(repository_root),
        "tracked_bytes": tracked_bytes,
        "tracked_files": tracked_files,
    }


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_manifest_projection(
    *,
    candidate_id: str,
    lease_id: str,
    repository_root: Path,
    base_sha: str,
    profile_id: str,
    child_id: str,
    child_status: SubagentStatus,
    evidence_sha256: str,
    disposition: CandidateDisposition,
    files: tuple[CandidateFile, ...],
    observed_paths: tuple[str, ...],
    changed_files: int,
    after_content_bytes: int,
    rejection_reasons: tuple[str, ...],
) -> dict[str, object]:
    return {
        "after_content_bytes": after_content_bytes,
        "base_sha": base_sha,
        "candidate_id": candidate_id,
        "changed_files": changed_files,
        "child_id": child_id,
        "child_status": child_status.value,
        "disposition": disposition.value,
        "evidence_sha256": evidence_sha256,
        "files": [item.model_dump(mode="json") for item in files],
        "lease_id": lease_id,
        "observed_paths": list(observed_paths),
        "profile_id": profile_id,
        "rejection_reasons": list(rejection_reasons),
        "repository_root": str(repository_root),
    }


def _implementation_run_projection(
    *,
    profile_id: str,
    child: SubagentChildResult,
    finalization: WorktreeFinalizationResult,
    duration_ms: int,
) -> dict[str, object]:
    return {
        "child": child.model_dump(mode="json"),
        "duration_ms": duration_ms,
        "finalization": finalization.model_dump(mode="json"),
        "profile_id": profile_id,
    }
