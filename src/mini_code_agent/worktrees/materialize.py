from __future__ import annotations

import hashlib
import os
import stat
from contextlib import suppress
from pathlib import Path

from mini_code_agent.worktrees.models import (
    GitIndexEntry,
    GitIndexPointer,
    WorktreeLimits,
)


class MaterializationError(RuntimeError):
    pass


def read_worktree_admin_dir(root: Path) -> Path:
    git_file = root / ".git"
    if _is_link_or_reparse(git_file):
        raise MaterializationError("Worktree administrative file cannot be a link.")
    try:
        content = git_file.read_bytes()
    except OSError:
        raise MaterializationError("Worktree administrative file is unavailable.") from None
    if (
        not content.endswith(b"\n")
        or content.count(b"\n") != 1
        or len(content) > 4096
        or not content.startswith(b"gitdir: ")
    ):
        raise MaterializationError("Worktree administrative file is invalid.")
    try:
        raw_path = content[len(b"gitdir: ") : -1].decode("utf-8")
    except UnicodeDecodeError:
        raise MaterializationError("Worktree administrative file is invalid.") from None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise MaterializationError("Worktree administrative directory is unavailable.") from None
    if _is_link_or_reparse(resolved) or not resolved.is_dir():
        raise MaterializationError("Worktree administrative directory is unsafe.")
    return resolved


def materialize_index(
    root: Path,
    pointers: tuple[GitIndexPointer, ...],
    blobs: dict[str, bytes],
    *,
    limits: WorktreeLimits,
) -> tuple[GitIndexEntry, ...]:
    resolved_root = _verify_initial_root(root)
    ordered = tuple(sorted(pointers, key=lambda pointer: pointer.path))
    if len(ordered) > limits.max_tracked_files:
        raise MaterializationError("Tracked file count exceeds the lease limit.")
    if len({pointer.path.casefold() for pointer in ordered}) != len(ordered):
        raise MaterializationError("Tracked paths collide.")
    required_objects = {pointer.object_id for pointer in ordered}
    if set(blobs) != required_objects:
        raise MaterializationError("Tracked blobs do not match the index.")
    total_bytes = sum(len(blobs[pointer.object_id]) for pointer in ordered)
    if total_bytes > limits.max_tracked_bytes:
        raise MaterializationError("Tracked bytes exceed the lease limit.")

    entries: list[GitIndexEntry] = []
    try:
        for pointer in ordered:
            if len(Path(pointer.path).parts) > limits.max_tracked_depth:
                raise MaterializationError("Tracked path depth exceeds the lease limit.")
            content = blobs[pointer.object_id]
            target = resolved_root.joinpath(*pointer.path.split("/"))
            parent = _ensure_parent_directories(resolved_root, target.parent)
            _write_regular_file(parent, target, content, pointer.mode)
            entries.append(
                GitIndexEntry(
                    path=pointer.path,
                    mode=pointer.mode,
                    object_id=pointer.object_id,
                    byte_count=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
    except MaterializationError:
        raise
    except OSError:
        raise MaterializationError("Tracked files could not be materialized.") from None
    return tuple(entries)


def _verify_initial_root(root: Path) -> Path:
    if _is_link_or_reparse(root):
        raise MaterializationError("Worktree root cannot be a link.")
    try:
        resolved = root.resolve(strict=True)
        mode = root.stat(follow_symlinks=False).st_mode
        children = tuple(root.iterdir())
    except OSError:
        raise MaterializationError("Worktree root is unavailable.") from None
    if not stat.S_ISDIR(mode):
        raise MaterializationError("Worktree root is not a directory.")
    if len(children) != 1 or children[0].name != ".git":
        raise MaterializationError("No-checkout Worktree contains unexpected files.")
    git_file = children[0]
    if _is_link_or_reparse(git_file):
        raise MaterializationError("Worktree administrative file cannot be a link.")
    try:
        if not stat.S_ISREG(git_file.stat(follow_symlinks=False).st_mode):
            raise MaterializationError("Worktree administrative path is invalid.")
    except OSError:
        raise MaterializationError("Worktree administrative path is unavailable.") from None
    read_worktree_admin_dir(resolved)
    return resolved


def _ensure_parent_directories(root: Path, parent: Path) -> Path:
    try:
        relative = parent.relative_to(root)
    except ValueError:
        raise MaterializationError("Tracked path escaped the Worktree.") from None
    current = root
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise MaterializationError("Tracked parent directory could not be created.") from None
        if _is_link_or_reparse(current):
            raise MaterializationError("Tracked path traverses a link.")
        try:
            if not stat.S_ISDIR(current.stat(follow_symlinks=False).st_mode):
                raise MaterializationError("Tracked parent path is not a directory.")
        except OSError:
            raise MaterializationError("Tracked parent directory is unavailable.") from None
    return current


def _write_regular_file(
    parent: Path,
    target: Path,
    content: bytes,
    mode: str,
) -> None:
    if _is_link_or_reparse(parent):
        raise MaterializationError("Tracked parent directory became unsafe.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            target.chmod(0o755 if mode == "100755" else 0o644, follow_symlinks=False)
    except (FileExistsError, OSError):
        raise MaterializationError("Tracked file could not be created safely.") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _is_link_or_reparse(target):
        with suppress(OSError):
            target.unlink()
        raise MaterializationError("Tracked file became a link.")
    try:
        metadata = target.stat(follow_symlinks=False)
    except OSError:
        raise MaterializationError("Tracked file could not be verified.") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(content):
        raise MaterializationError("Tracked file verification failed.")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)
