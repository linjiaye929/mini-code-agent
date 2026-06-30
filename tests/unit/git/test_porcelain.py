from __future__ import annotations

import pytest

from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import GitEntryKind
from mini_code_agent.git.porcelain import parse_porcelain_v2

OID = "a" * 40
HEAD1 = "1" * 40
HEAD2 = "2" * 40
HEAD3 = "3" * 40


def status_output(*records: str) -> str:
    return "\0".join(
        (
            f"# branch.oid {OID}",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -3",
            *records,
            "",
        )
    )


def test_parse_porcelain_v2_all_supported_record_kinds() -> None:
    output = status_output(
        f"1 .M N... 100644 100644 100644 {HEAD1} {HEAD2} src/app.py",
        f"2 R. N... 100644 100644 100644 {HEAD1} {HEAD2} R100 new name.py",
        "old name.py",
        (f"u UU N... 100644 100644 100644 100644 {HEAD1} {HEAD2} {HEAD3} conflict.py"),
        "? -new\tfile\n.txt",
    )

    snapshot = parse_porcelain_v2(output, max_entries=10)

    assert snapshot.branch_oid == OID
    assert snapshot.branch_head == "main"
    assert snapshot.branch_upstream == "origin/main"
    assert snapshot.ahead == 2
    assert snapshot.behind == 3
    assert tuple(entry.kind for entry in snapshot.entries) == (
        GitEntryKind.ORDINARY,
        GitEntryKind.RENAMED,
        GitEntryKind.UNMERGED,
        GitEntryKind.UNTRACKED,
    )
    assert snapshot.entries[1].path == "new name.py"
    assert snapshot.entries[1].original_path == "old name.py"
    assert snapshot.entries[3].path == "-new\tfile\n.txt"


def test_parse_porcelain_v2_supports_unborn_and_detached_without_upstream() -> None:
    output = "\0".join(
        (
            "# branch.oid (initial)",
            "# branch.head (detached)",
            "? first.txt",
            "",
        )
    )

    snapshot = parse_porcelain_v2(output, max_entries=1)

    assert snapshot.branch_oid == "(initial)"
    assert snapshot.branch_head == "(detached)"
    assert snapshot.branch_upstream is None
    assert snapshot.ahead == 0
    assert snapshot.behind == 0


@pytest.mark.parametrize(
    "output",
    [
        "",
        f"# branch.oid {OID}\0",
        f"# branch.oid {OID}\0# branch.oid {OID}\0# branch.head main\0",
        f"# branch.oid {OID}\0# branch.head main\0# branch.ab +1 -0\0",
        f"# branch.oid {OID}\0# branch.head main\0! ignored.txt\0",
        f"# branch.oid {OID}\0# branch.head main\x002 R. N... short\0",
        f"# branch.oid {OID}\0# branch.head main\x002 R. N... x\0new.txt\0",
        f"# branch.oid {OID}\0# branch.head main\0x unknown\0",
        f"# branch.oid {OID}\0# branch.head main\0? bad\ufffdpath\0",
    ],
)
def test_parse_porcelain_v2_rejects_malformed_or_unsafe_output(
    output: str,
) -> None:
    with pytest.raises(GitError) as captured:
        parse_porcelain_v2(output, max_entries=10)

    assert captured.value.code is GitErrorCode.INVALID_OUTPUT


def test_parse_porcelain_v2_enforces_entry_limit_at_n_plus_one() -> None:
    output = status_output("? one.txt", "? two.txt")

    assert len(parse_porcelain_v2(output, max_entries=2).entries) == 2
    with pytest.raises(GitError) as captured:
        parse_porcelain_v2(output, max_entries=1)

    assert captured.value.code is GitErrorCode.LIMIT_EXCEEDED
