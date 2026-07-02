from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from mini_code_agent.worktrees.models import (
    CandidateDisposition,
    CandidateManifest,
    CandidateState,
    WorktreeProfile,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ROOT = "candidates"


class WorktreeStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LeasePaths:
    container: Path
    worktree: Path


@dataclass(frozen=True, slots=True)
class VerifiedCandidate:
    manifest: CandidateManifest
    blobs: dict[str, bytes]


class WorktreeStateStore:
    def __init__(self, profile: WorktreeProfile) -> None:
        self._profile = profile
        self._root = profile.state_root

    @property
    def root(self) -> Path:
        return self._root

    def initialize(self) -> None:
        self._verify_directory(self._root)
        self._ensure_managed_directory(self._root / "leases")
        self._ensure_managed_directory(self._root / "hooks-empty")
        candidate_root = self._root / _CANDIDATE_ROOT
        self._ensure_managed_directory(candidate_root)
        for state in CandidateState:
            self._ensure_managed_directory(candidate_root / state.value)

    def begin_candidate(self, candidate_id: str) -> Path:
        self._validate_identifier(candidate_id)
        self._verify_layout()
        if any(self._candidate_path(state, candidate_id).exists() for state in CandidateState):
            raise WorktreeStateError("Candidate identifier already exists.")
        path = self._candidate_path(CandidateState.BUILDING, candidate_id)
        try:
            path.mkdir(mode=0o700)
        except OSError:
            raise WorktreeStateError("Candidate directory could not be created.") from None
        self._verify_directory(path)
        self._ensure_managed_directory(path / "blobs")
        return path

    def active_lease_ids(self) -> tuple[str, ...]:
        leases = self._root / "leases"
        self._verify_directory(leases)
        identifiers: list[str] = []
        try:
            children = tuple(leases.iterdir())
        except OSError:
            raise WorktreeStateError("Lease state could not be listed.") from None
        for child in children:
            self._validate_identifier(child.name)
            self._verify_directory(child)
            identifiers.append(child.name)
        return tuple(sorted(identifiers))

    def begin_lease(self, lease_id: str) -> LeasePaths:
        self._validate_identifier(lease_id)
        self._verify_directory(self._root / "leases")
        container = self._root / "leases" / lease_id
        try:
            container.mkdir(mode=0o700)
            if os.name != "nt":
                container.chmod(0o700)
        except OSError:
            raise WorktreeStateError("Lease directory could not be created.") from None
        self._verify_directory(container)
        return LeasePaths(container=container, worktree=container / "worktree")

    def write_lease_json(
        self,
        lease_id: str,
        filename: str,
        payload: object,
    ) -> Path:
        self._validate_identifier(lease_id)
        if filename not in {"base-manifest.json", "lease.json"}:
            raise WorktreeStateError("Lease JSON filename is invalid.")
        container = self._root / "leases" / lease_id
        self._verify_directory(container)
        try:
            encoded = (
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise WorktreeStateError("Lease JSON payload is invalid.") from None
        target = container / filename
        self._publish_immutable(target, encoded)
        return target

    def abandon_empty_lease(self, lease_id: str) -> None:
        self._validate_identifier(lease_id)
        container = self._root / "leases" / lease_id
        self._verify_directory(container)
        try:
            if any(container.iterdir()):
                raise WorktreeStateError("Non-empty lease cannot be abandoned.")
            container.rmdir()
        except WorktreeStateError:
            raise
        except OSError:
            raise WorktreeStateError("Empty lease could not be abandoned.") from None

    def write_candidate_json(
        self,
        candidate_id: str,
        filename: str,
        payload: object,
    ) -> Path:
        candidate = self._building_candidate(candidate_id)
        if (
            not filename.endswith(".json")
            or filename in {".json", "..json"}
            or "/" in filename
            or "\\" in filename
            or "\0" in filename
        ):
            raise WorktreeStateError("Candidate JSON filename is invalid.")
        try:
            encoded = (
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise WorktreeStateError("Candidate JSON payload is invalid.") from None
        target = candidate / filename
        self._publish_immutable(target, encoded)
        return target

    def write_candidate_blob(
        self,
        candidate_id: str,
        digest: str,
        content: bytes,
    ) -> Path:
        candidate = self._building_candidate(candidate_id)
        if _SHA256.fullmatch(digest) is None or hashlib.sha256(content).hexdigest() != digest:
            raise WorktreeStateError("Candidate blob hash is invalid.")
        if len(content) > self._profile.limits.max_file_bytes:
            raise WorktreeStateError("Candidate blob exceeds the file limit.")
        target = candidate / "blobs" / digest
        self._publish_immutable(target, content)
        return target

    def transition_candidate(
        self,
        candidate_id: str,
        source: CandidateState,
        target: CandidateState,
    ) -> Path:
        self._validate_identifier(candidate_id)
        self._verify_layout()
        source_path = self._candidate_path(source, candidate_id)
        target_path = self._candidate_path(target, candidate_id)
        self._verify_directory(source_path)
        if target_path.exists():
            raise WorktreeStateError("Candidate target state already exists.")
        try:
            source_path.rename(target_path)
        except OSError:
            raise WorktreeStateError("Candidate state transition failed.") from None
        self._verify_directory(target_path)
        return target_path

    def load_candidate(
        self,
        state: CandidateState,
        candidate_id: str,
    ) -> CandidateManifest:
        self._validate_identifier(candidate_id)
        self._verify_layout()
        candidate = self._candidate_path(state, candidate_id)
        self._verify_directory(candidate)
        try:
            candidate_children = {path.name for path in candidate.iterdir()}
        except OSError:
            raise WorktreeStateError("Candidate directory could not be listed.") from None
        expected_children = {"manifest.json", "blobs"}
        allowed_children = (
            {frozenset(expected_children), frozenset({*expected_children, "recovery.json"})}
            if state in {CandidateState.APPLYING, CandidateState.UNCERTAIN}
            else {frozenset(expected_children)}
        )
        if frozenset(candidate_children) not in allowed_children:
            raise WorktreeStateError("Candidate directory contains unexpected paths.")
        manifest_path = candidate / "manifest.json"
        if _is_link_or_reparse(manifest_path):
            raise WorktreeStateError("Candidate manifest is unsafe.")
        try:
            if manifest_path.stat(follow_symlinks=False).st_size > 16 * 1024 * 1024:
                raise WorktreeStateError("Candidate manifest exceeds its limit.")
            manifest = CandidateManifest.model_validate_json(manifest_path.read_bytes())
        except WorktreeStateError:
            raise
        except (OSError, ValidationError, ValueError):
            raise WorktreeStateError("Candidate manifest is invalid.") from None
        expected_disposition = {
            CandidateState.READY: CandidateDisposition.READY,
            CandidateState.REJECTED: CandidateDisposition.REJECTED,
            CandidateState.APPLYING: CandidateDisposition.READY,
            CandidateState.APPLIED: CandidateDisposition.READY,
            CandidateState.DISCARDING: CandidateDisposition.READY,
            CandidateState.UNCERTAIN: CandidateDisposition.READY,
        }.get(state)
        if (
            manifest.candidate_id != candidate_id
            or expected_disposition is None
            or manifest.disposition is not expected_disposition
            or manifest.repository_root != self._profile.repository_root
            or manifest.profile_id != self._profile.implementation_profile.profile_id
            or manifest.changed_files > self._profile.limits.max_candidate_files
            or manifest.after_content_bytes > self._profile.limits.max_candidate_after_bytes
            or (
                state is not CandidateState.REJECTED
                and any(
                    not _is_allowed_candidate_path(
                        path,
                        self._profile.allowed_path_prefixes,
                    )
                    for path in manifest.observed_paths
                )
            )
        ):
            raise WorktreeStateError("Candidate state and manifest do not match.")
        blobs = candidate / "blobs"
        self._verify_directory(blobs)
        expected_hashes = {item.content_blob_sha256 for item in manifest.files}
        try:
            actual = tuple(blobs.iterdir())
        except OSError:
            raise WorktreeStateError("Candidate blobs could not be listed.") from None
        if {path.name for path in actual} != expected_hashes:
            raise WorktreeStateError("Candidate blob set is invalid.")
        for path in actual:
            if _is_link_or_reparse(path):
                raise WorktreeStateError("Candidate blob is unsafe.")
            try:
                content = path.read_bytes()
            except OSError:
                raise WorktreeStateError("Candidate blob could not be read.") from None
            if (
                len(content) > self._profile.limits.max_file_bytes
                or hashlib.sha256(content).hexdigest() != path.name
            ):
                raise WorktreeStateError("Candidate blob hash is invalid.")
        return manifest

    def load_candidate_payload(
        self,
        state: CandidateState,
        candidate_id: str,
    ) -> VerifiedCandidate:
        manifest = self.load_candidate(state, candidate_id)
        blobs_dir = self._candidate_path(state, candidate_id) / "blobs"
        blobs: dict[str, bytes] = {}
        for digest in sorted({item.content_blob_sha256 for item in manifest.files}):
            path = blobs_dir / digest
            try:
                blobs[digest] = path.read_bytes()
            except OSError:
                raise WorktreeStateError("Candidate blob could not be read.") from None
        return VerifiedCandidate(manifest=manifest, blobs=blobs)

    def delete_candidate(
        self,
        state: CandidateState,
        candidate_id: str,
    ) -> None:
        if state is not CandidateState.DISCARDING:
            raise WorktreeStateError("Only a claimed discard candidate can be deleted.")
        payload = self.load_candidate_payload(state, candidate_id)
        candidate = self._candidate_path(state, candidate_id)
        blobs = candidate / "blobs"
        for digest in sorted(payload.blobs):
            path = blobs / digest
            if _is_link_or_reparse(path):
                raise WorktreeStateError("Candidate blob is unsafe.")
            try:
                path.unlink()
            except OSError:
                raise WorktreeStateError("Candidate blob could not be removed.") from None
        try:
            blobs.rmdir()
            (candidate / "manifest.json").unlink()
            candidate.rmdir()
        except OSError:
            raise WorktreeStateError("Candidate could not be removed.") from None

    def write_candidate_recovery(
        self,
        state: CandidateState,
        candidate_id: str,
        status: str,
    ) -> None:
        if state is not CandidateState.APPLYING or status not in {"apply_uncertain"}:
            raise WorktreeStateError("Candidate recovery evidence is invalid.")
        self.load_candidate(state, candidate_id)
        candidate = self._candidate_path(state, candidate_id)
        target = candidate / "recovery.json"
        if target.exists():
            return
        encoded = (
            json.dumps(
                {
                    "candidate_id": candidate_id,
                    "status": status,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
        self._publish_immutable(target, encoded)

    def complete_lease(self, lease_id: str) -> None:
        self._validate_identifier(lease_id)
        leases = self._root / "leases"
        self._verify_directory(leases)
        container = leases / lease_id
        self._verify_directory(container)
        if (container / "worktree").exists():
            raise WorktreeStateError("Active Worktree lease cannot be completed.")
        try:
            children = tuple(container.iterdir())
        except OSError:
            raise WorktreeStateError("Lease state could not be listed.") from None
        allowed = {"base-manifest.json", "lease.json", "cleanup-required.json"}
        if any(child.name not in allowed for child in children):
            raise WorktreeStateError("Lease state contains unexpected paths.")
        for child in children:
            if _is_link_or_reparse(child):
                raise WorktreeStateError("Lease metadata is unsafe.")
            try:
                if not stat.S_ISREG(child.stat(follow_symlinks=False).st_mode):
                    raise WorktreeStateError("Lease metadata is not a regular file.")
                child.unlink()
            except WorktreeStateError:
                raise
            except OSError:
                raise WorktreeStateError("Lease metadata could not be removed.") from None
        try:
            container.rmdir()
        except OSError:
            raise WorktreeStateError("Lease directory could not be removed.") from None

    def record_cleanup_required(self, lease_id: str, stage: str) -> None:
        self._validate_identifier(lease_id)
        if stage not in {
            "snapshot_failed",
            "cleanup_failed",
            "cancellation_timeout",
            "creation_failed",
        }:
            raise WorktreeStateError("Cleanup diagnostic stage is invalid.")
        container = self._root / "leases" / lease_id
        self._verify_directory(container)
        target = container / "cleanup-required.json"
        if target.exists():
            return
        payload = (
            json.dumps(
                {
                    "lease_id": lease_id,
                    "stage": stage,
                    "status": "cleanup_required",
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
        self._publish_immutable(target, payload)

    def _building_candidate(self, candidate_id: str) -> Path:
        self._validate_identifier(candidate_id)
        self._verify_layout()
        candidate = self._candidate_path(CandidateState.BUILDING, candidate_id)
        self._verify_directory(candidate)
        self._verify_directory(candidate / "blobs")
        return candidate

    def _candidate_path(self, state: CandidateState, candidate_id: str) -> Path:
        return self._root / _CANDIDATE_ROOT / state.value / candidate_id

    def _verify_layout(self) -> None:
        self._verify_directory(self._root)
        self._verify_directory(self._root / _CANDIDATE_ROOT)
        for state in CandidateState:
            self._verify_directory(self._root / _CANDIDATE_ROOT / state.value)

    @staticmethod
    def _validate_identifier(identifier: str) -> None:
        if _IDENTIFIER.fullmatch(identifier) is None:
            raise WorktreeStateError("State identifier is invalid.")

    @staticmethod
    def _ensure_managed_directory(path: Path) -> None:
        try:
            path.mkdir(mode=0o700, exist_ok=True)
            if os.name != "nt":
                path.chmod(0o700)
        except OSError:
            raise WorktreeStateError("Managed state directory could not be created.") from None
        WorktreeStateStore._verify_directory(path)

    @staticmethod
    def _verify_directory(path: Path) -> None:
        if _is_link_or_reparse(path):
            raise WorktreeStateError("Managed state path cannot be a link.")
        try:
            mode = path.stat(follow_symlinks=False).st_mode
        except OSError:
            raise WorktreeStateError("Managed state directory is unavailable.") from None
        if not stat.S_ISDIR(mode):
            raise WorktreeStateError("Managed state path is not a directory.")

    @staticmethod
    def _publish_immutable(target: Path, content: bytes) -> None:
        WorktreeStateStore._verify_directory(target.parent)
        if target.exists():
            raise WorktreeStateError("Immutable state file already exists.")
        descriptor = -1
        temp_path: Path | None = None
        try:
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=".mini-code-agent-",
                suffix=".tmp",
                dir=target.parent,
            )
            temp_path = Path(raw_temp)
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp_path, target)
            temp_path.unlink()
            temp_path = None
            _fsync_directory(target.parent)
        except (FileExistsError, OSError):
            raise WorktreeStateError("Immutable state file could not be published.") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temp_path is not None:
                with suppress(OSError):
                    temp_path.unlink()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_allowed_candidate_path(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)
