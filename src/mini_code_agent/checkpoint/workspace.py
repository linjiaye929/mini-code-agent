from __future__ import annotations

import hashlib
import json
import os
import stat
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from mini_code_agent.checkpoint.models import CheckpointLimits, WorkspaceScanConfig

_CHUNK_SIZE = 1024 * 1024


class _HashWriter(Protocol):
    def update(self, data: bytes, /) -> None: ...


class WorkspaceFingerprintErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    UNSAFE_ENTRY = "unsafe_entry"
    LIMIT_EXCEEDED = "limit_exceeded"
    CHANGED_DURING_SCAN = "changed_during_scan"


class WorkspaceFingerprintError(RuntimeError):
    def __init__(self, code: WorkspaceFingerprintErrorCode) -> None:
        super().__init__("Workspace fingerprint could not be computed.")
        self.code = code


def workspace_sha256(
    root: Path,
    *,
    limits: CheckpointLimits | None = None,
    config: WorkspaceScanConfig | None = None,
) -> str:
    active_limits = limits or CheckpointLimits()
    active_config = config or WorkspaceScanConfig()
    try:
        root_stat = root.lstat()
    except OSError:
        raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.UNAVAILABLE) from None
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.UNAVAILABLE)

    manifest = hashlib.sha256()
    manifest.update(b"mini-code-agent-workspace-v1\n")
    manifest.update(
        _canonical_json(
            {"excluded_directory_names": sorted(active_config.excluded_directory_names)}
        )
    )
    manifest.update(b"\n")
    counters = _ScanCounters()
    _scan_directory(
        root,
        root,
        active_limits,
        active_config,
        counters,
        manifest,
    )
    return manifest.hexdigest()


class _ScanCounters:
    __slots__ = ("bytes", "files")

    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0


def _scan_directory(
    root: Path,
    directory: Path,
    limits: CheckpointLimits,
    config: WorkspaceScanConfig,
    counters: _ScanCounters,
    manifest: _HashWriter,
) -> None:
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
    except OSError:
        raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.UNAVAILABLE) from None

    for entry in entries:
        try:
            if entry.is_symlink():
                raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.UNSAFE_ENTRY)
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in config.excluded_directory_names:
                    _scan_directory(
                        root,
                        Path(entry.path),
                        limits,
                        config,
                        counters,
                        manifest,
                    )
                continue
            if not entry.is_file(follow_symlinks=False):
                raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.UNSAFE_ENTRY)
            before = entry.stat(follow_symlinks=False)
        except WorkspaceFingerprintError:
            raise
        except OSError:
            raise WorkspaceFingerprintError(
                WorkspaceFingerprintErrorCode.CHANGED_DURING_SCAN
            ) from None

        counters.files += 1
        if counters.files > limits.max_workspace_files:
            raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.LIMIT_EXCEEDED)
        relative = Path(entry.path).relative_to(root).as_posix()
        digest, size = _hash_regular_file(Path(entry.path), before)
        counters.bytes += size
        if counters.bytes > limits.max_workspace_bytes:
            raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.LIMIT_EXCEEDED)
        manifest.update(
            _canonical_json(
                {
                    "path": relative,
                    "sha256": digest,
                    "size": size,
                }
            )
        )
        manifest.update(b"\n")


def _hash_regular_file(path: Path, before: os.stat_result) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not _same_file(before, opened) or not stat.S_ISREG(opened.st_mode):
                raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.CHANGED_DURING_SCAN)
            while chunk := handle.read(_CHUNK_SIZE):
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except WorkspaceFingerprintError:
        raise
    except OSError:
        raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.CHANGED_DURING_SCAN) from None
    if (
        not _same_file(opened, after)
        or opened.st_size != size
        or after.st_size != size
        or opened.st_mtime_ns != after.st_mtime_ns
    ):
        raise WorkspaceFingerprintError(WorkspaceFingerprintErrorCode.CHANGED_DURING_SCAN)
    return digest.hexdigest(), size


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    identity_matches = (
        left.st_ino == 0
        or right.st_ino == 0
        or left.st_dev == 0
        or right.st_dev == 0
        or (left.st_dev == right.st_dev and left.st_ino == right.st_ino)
    )
    return (
        identity_matches
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
