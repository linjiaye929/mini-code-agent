from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import (
    GitDiffMode,
    GitDiffResult,
    GitEntryKind,
    GitLimits,
    GitStatusEntry,
    GitStatusSnapshot,
    status_sha256,
)


def ordinary(path: str = "src/app.py") -> GitStatusEntry:
    return GitStatusEntry(
        kind=GitEntryKind.ORDINARY,
        index_status=".",
        worktree_status="M",
        path=path,
        submodule="N...",
    )


def test_git_limits_are_bounded_and_immutable() -> None:
    limits = GitLimits()

    assert limits.command_timeout_seconds == 10
    assert limits.max_output_bytes == 2 * 1024 * 1024
    assert limits.max_status_entries == 10_000
    assert limits.max_patch_chars == 2 * 1024 * 1024
    with pytest.raises(ValidationError):
        limits.max_status_entries = 2
    with pytest.raises(ValidationError):
        GitLimits(command_timeout_seconds=0)
    with pytest.raises(ValidationError):
        GitLimits(max_output_bytes=1_023)


def test_git_status_entry_validates_kind_status_and_paths() -> None:
    renamed = GitStatusEntry(
        kind=GitEntryKind.RENAMED,
        index_status="R",
        worktree_status=".",
        path="new name.py",
        original_path="old name.py",
        submodule="N...",
    )
    untracked = GitStatusEntry(
        kind=GitEntryKind.UNTRACKED,
        index_status="?",
        worktree_status="?",
        path="-leading-option.txt",
    )

    assert renamed.original_path == "old name.py"
    assert untracked.path == "-leading-option.txt"
    with pytest.raises(ValidationError):
        GitStatusEntry(
            kind=GitEntryKind.RENAMED,
            index_status="R",
            worktree_status=".",
            path="new.py",
            submodule="N...",
        )
    with pytest.raises(ValidationError):
        ordinary("bad\0path")
    with pytest.raises(ValidationError):
        GitStatusEntry(
            kind=GitEntryKind.UNTRACKED,
            index_status=".",
            worktree_status="?",
            path="wrong.txt",
        )


def test_status_snapshot_verifies_canonical_fingerprint() -> None:
    entries = (ordinary(),)
    fingerprint = status_sha256(
        branch_oid="a" * 40,
        branch_head="main",
        branch_upstream="origin/main",
        ahead=1,
        behind=2,
        entries=entries,
    )
    snapshot = GitStatusSnapshot(
        branch_oid="a" * 40,
        branch_head="main",
        branch_upstream="origin/main",
        ahead=1,
        behind=2,
        entries=entries,
        sha256=fingerprint,
    )

    assert snapshot.sha256 == fingerprint
    with pytest.raises(ValidationError):
        GitStatusSnapshot(
            branch_oid="a" * 40,
            branch_head="main",
            branch_upstream="origin/main",
            ahead=1,
            behind=2,
            entries=entries,
            sha256="0" * 64,
        )


def test_diff_result_verifies_counts_and_hash() -> None:
    patch = "diff --git a/a.py b/a.py\n+changed\n"
    encoded = patch.encode("utf-8")
    result = GitDiffResult(
        mode=GitDiffMode.UNSTAGED,
        patch=patch,
        byte_count=len(encoded),
        char_count=len(patch),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )

    assert result.mode is GitDiffMode.UNSTAGED
    with pytest.raises(ValidationError):
        GitDiffResult(
            mode=GitDiffMode.STAGED,
            patch=patch,
            byte_count=1,
            char_count=len(patch),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )


def test_git_error_has_static_public_contract() -> None:
    error = GitError(GitErrorCode.NOT_REPOSITORY)

    assert str(error) == "Git operation could not be completed."
    assert error.public_message == "Git operation could not be completed."
    assert error.code is GitErrorCode.NOT_REPOSITORY
