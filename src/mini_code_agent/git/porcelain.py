from __future__ import annotations

import re

from pydantic import ValidationError

from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import (
    GitEntryKind,
    GitStatusEntry,
    GitStatusSnapshot,
    status_sha256,
)

_AB_PATTERN = re.compile(r"^\+([0-9]+) -([0-9]+)$")
_SCORE_PATTERN = re.compile(r"^[RC][0-9]{1,3}$")


def parse_porcelain_v2(
    output: str,
    *,
    max_entries: int,
) -> GitStatusSnapshot:
    if not 1 <= max_entries <= 100_000 or "\ufffd" in output:
        raise _invalid_output()
    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if not fields or any(field == "" for field in fields):
        raise _invalid_output()

    branch: dict[str, str] = {}
    entries: list[GitStatusEntry] = []
    index = 0
    entries_started = False
    try:
        while index < len(fields):
            record = fields[index]
            if record.startswith("# "):
                if entries_started:
                    raise _invalid_output()
                _parse_branch_header(record, branch)
                index += 1
                continue
            entries_started = True
            if record.startswith("1 "):
                entry = _parse_ordinary(record)
            elif record.startswith("2 "):
                if index + 1 >= len(fields):
                    raise _invalid_output()
                entry = _parse_renamed(record, fields[index + 1])
                index += 1
            elif record.startswith("u "):
                entry = _parse_unmerged(record)
            elif record.startswith("? "):
                entry = GitStatusEntry(
                    kind=GitEntryKind.UNTRACKED,
                    index_status="?",
                    worktree_status="?",
                    path=record[2:],
                )
            else:
                raise _invalid_output()
            entries.append(entry)
            if len(entries) > max_entries:
                raise GitError(GitErrorCode.LIMIT_EXCEEDED)
            index += 1

        oid = branch["oid"]
        head = branch["head"]
        upstream = branch.get("upstream")
        ahead, behind = _parse_ab(branch.get("ab"), upstream)
        typed_entries = tuple(entries)
        fingerprint = status_sha256(
            branch_oid=oid,
            branch_head=head,
            branch_upstream=upstream,
            ahead=ahead,
            behind=behind,
            entries=typed_entries,
        )
        return GitStatusSnapshot(
            branch_oid=oid,
            branch_head=head,
            branch_upstream=upstream,
            ahead=ahead,
            behind=behind,
            entries=typed_entries,
            sha256=fingerprint,
        )
    except GitError:
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        raise _invalid_output() from None


def _parse_branch_header(record: str, branch: dict[str, str]) -> None:
    prefixes = {
        "# branch.oid ": "oid",
        "# branch.head ": "head",
        "# branch.upstream ": "upstream",
        "# branch.ab ": "ab",
    }
    for prefix, key in prefixes.items():
        if record.startswith(prefix):
            value = record[len(prefix) :]
            if not value or key in branch:
                raise _invalid_output()
            branch[key] = value
            return
    raise _invalid_output()


def _parse_ordinary(record: str) -> GitStatusEntry:
    parts = record.split(" ", 8)
    if len(parts) != 9:
        raise _invalid_output()
    return GitStatusEntry(
        kind=GitEntryKind.ORDINARY,
        index_status=_xy(parts[1])[0],
        worktree_status=_xy(parts[1])[1],
        submodule=parts[2],
        path=parts[8],
    )


def _parse_renamed(record: str, original_path: str) -> GitStatusEntry:
    parts = record.split(" ", 9)
    if len(parts) != 10 or _SCORE_PATTERN.fullmatch(parts[8]) is None or not original_path:
        raise _invalid_output()
    xy = _xy(parts[1])
    return GitStatusEntry(
        kind=GitEntryKind.RENAMED,
        index_status=xy[0],
        worktree_status=xy[1],
        submodule=parts[2],
        path=parts[9],
        original_path=original_path,
    )


def _parse_unmerged(record: str) -> GitStatusEntry:
    parts = record.split(" ", 10)
    if len(parts) != 11:
        raise _invalid_output()
    xy = _xy(parts[1])
    return GitStatusEntry(
        kind=GitEntryKind.UNMERGED,
        index_status=xy[0],
        worktree_status=xy[1],
        submodule=parts[2],
        path=parts[10],
    )


def _xy(value: str) -> str:
    if len(value) != 2:
        raise _invalid_output()
    return value


def _parse_ab(value: str | None, upstream: str | None) -> tuple[int, int]:
    if value is None:
        return 0, 0
    if upstream is None:
        raise _invalid_output()
    match = _AB_PATTERN.fullmatch(value)
    if match is None:
        raise _invalid_output()
    return int(match.group(1)), int(match.group(2))


def _invalid_output() -> GitError:
    return GitError(GitErrorCode.INVALID_OUTPUT)
