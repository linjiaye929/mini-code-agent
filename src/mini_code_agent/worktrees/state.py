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

from mini_code_agent.worktrees.models import CandidateState, WorktreeProfile

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ROOT = "candidates"


class WorktreeStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LeasePaths:
    container: Path
    worktree: Path


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
