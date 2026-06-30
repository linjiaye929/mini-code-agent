from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_VALID_STATUS = frozenset(".MTADRCU")
_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64}|\(initial\))$")


class GitLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_timeout_seconds: int = Field(default=10, ge=1, le=60)
    max_output_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1_024,
        le=16 * 1024 * 1024,
    )
    max_status_entries: int = Field(default=10_000, ge=1, le=100_000)
    max_patch_chars: int = Field(
        default=2 * 1024 * 1024,
        ge=1_024,
        le=16 * 1024 * 1024,
    )


class GitEntryKind(StrEnum):
    ORDINARY = "ordinary"
    RENAMED = "renamed"
    UNMERGED = "unmerged"
    UNTRACKED = "untracked"


class GitStatusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: GitEntryKind
    index_status: str = Field(min_length=1, max_length=1)
    worktree_status: str = Field(min_length=1, max_length=1)
    path: str = Field(min_length=1, max_length=4_096)
    original_path: str | None = Field(default=None, min_length=1, max_length=4_096)
    submodule: str | None = Field(
        default=None,
        pattern=r"^(?:N\.\.\.|S[.C][.M][.U])$",
    )

    @field_validator("path", "original_path")
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        if value is not None and "\0" in value:
            raise ValueError("Git path cannot contain NUL")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        if self.kind is GitEntryKind.UNTRACKED:
            if (
                self.index_status != "?"
                or self.worktree_status != "?"
                or self.original_path is not None
                or self.submodule is not None
            ):
                raise ValueError("untracked Git entry is inconsistent")
            return self
        if self.index_status not in _VALID_STATUS or self.worktree_status not in _VALID_STATUS:
            raise ValueError("Git status characters are invalid")
        if (self.kind is GitEntryKind.RENAMED) != (self.original_path is not None):
            raise ValueError("Git rename metadata is inconsistent")
        if self.submodule is None:
            raise ValueError("tracked Git entry requires submodule metadata")
        return self


class GitStatusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    branch_oid: str = Field(min_length=1, max_length=64)
    branch_head: str = Field(min_length=1, max_length=1_024)
    branch_upstream: str | None = Field(default=None, min_length=1, max_length=1_024)
    ahead: int = Field(default=0, ge=0, le=2_000_000_000)
    behind: int = Field(default=0, ge=0, le=2_000_000_000)
    entries: tuple[GitStatusEntry, ...] = Field(default=(), max_length=100_000)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_metadata_and_hash(self) -> Self:
        if _OID.fullmatch(self.branch_oid) is None:
            raise ValueError("Git branch OID is invalid")
        if self.branch_upstream is None and (self.ahead != 0 or self.behind != 0):
            raise ValueError("Git branch counts require an upstream")
        expected = status_sha256(
            branch_oid=self.branch_oid,
            branch_head=self.branch_head,
            branch_upstream=self.branch_upstream,
            ahead=self.ahead,
            behind=self.behind,
            entries=self.entries,
        )
        if self.sha256 != expected:
            raise ValueError("Git status fingerprint is invalid")
        return self


class GitDiffMode(StrEnum):
    STAGED = "staged"
    UNSTAGED = "unstaged"


class GitDiffResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: GitDiffMode
    patch: str = Field(max_length=16 * 1024 * 1024)
    byte_count: int = Field(ge=0, le=32 * 1024 * 1024)
    char_count: int = Field(ge=0, le=16 * 1024 * 1024)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_counts_and_hash(self) -> Self:
        encoded = self.patch.encode("utf-8")
        if (
            self.byte_count != len(encoded)
            or self.char_count != len(self.patch)
            or self.sha256 != hashlib.sha256(encoded).hexdigest()
        ):
            raise ValueError("Git diff metadata is inconsistent")
        return self


def status_sha256(
    *,
    branch_oid: str,
    branch_head: str,
    branch_upstream: str | None,
    ahead: int,
    behind: int,
    entries: tuple[GitStatusEntry, ...],
) -> str:
    payload = {
        "ahead": ahead,
        "behind": behind,
        "branch_head": branch_head,
        "branch_oid": branch_oid,
        "branch_upstream": branch_upstream,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
