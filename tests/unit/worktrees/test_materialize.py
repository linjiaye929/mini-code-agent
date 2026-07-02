from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from mini_code_agent.worktrees.materialize import MaterializationError, materialize_index
from mini_code_agent.worktrees.models import GitIndexPointer, WorktreeLimits


def pointer(
    path: str,
    *,
    object_id: str = "a" * 40,
    mode: str = "100644",
) -> GitIndexPointer:
    return GitIndexPointer.model_validate(
        {"path": path, "object_id": object_id, "mode": mode, "stage": 0}
    )


def test_materializer_writes_raw_blobs_and_regular_modes(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: admin\n", encoding="utf-8")
    script = b"#!/usr/bin/env python\nprint('ok')\r\n"
    binary = b"\x00\xffraw"
    pointers = (
        pointer("src/run.py", object_id="a" * 40, mode="100755"),
        pointer("assets/raw.bin", object_id="b" * 40),
    )

    manifest = materialize_index(
        root,
        pointers,
        {"a" * 40: script, "b" * 40: binary},
        limits=WorktreeLimits(),
    )

    assert (root / "src" / "run.py").read_bytes() == script
    assert (root / "assets" / "raw.bin").read_bytes() == binary
    assert [entry.path for entry in manifest] == ["assets/raw.bin", "src/run.py"]
    assert manifest[0].sha256 == hashlib.sha256(binary).hexdigest()
    if os.name != "nt":
        assert (root / "src" / "run.py").stat().st_mode & stat.S_IXUSR
        assert not (root / "assets" / "raw.bin").stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    ("pointers", "blobs", "limits"),
    [
        (
            (pointer("src/missing.py"),),
            {},
            WorktreeLimits(),
        ),
        (
            (pointer("a/b/c/d.py"),),
            {"a" * 40: b"x"},
            WorktreeLimits(max_tracked_depth=3),
        ),
        (
            (pointer("large.py"),),
            {"a" * 40: b"12345"},
            WorktreeLimits(max_tracked_bytes=4),
        ),
        (
            (pointer("one.py"), pointer("two.py", object_id="b" * 40)),
            {"a" * 40: b"1", "b" * 40: b"2"},
            WorktreeLimits(max_tracked_files=1),
        ),
    ],
)
def test_materializer_rejects_missing_blobs_or_budget_excess(
    tmp_path: Path,
    pointers: tuple[GitIndexPointer, ...],
    blobs: dict[str, bytes],
    limits: WorktreeLimits,
) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: admin\n", encoding="utf-8")

    with pytest.raises(MaterializationError):
        materialize_index(root, pointers, blobs, limits=limits)


def test_materializer_rejects_unexpected_or_linked_worktree_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: admin\n", encoding="utf-8")
    (root / "unexpected").write_text("unsafe", encoding="utf-8")

    with pytest.raises(MaterializationError):
        materialize_index(
            root,
            (pointer("src/app.py"),),
            {"a" * 40: b"safe"},
            limits=WorktreeLimits(),
        )


def test_materializer_rejects_parent_directory_swap_to_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: admin\n", encoding="utf-8")

    def marks_src_as_link(path: Path) -> bool:
        return path.name == "src"

    monkeypatch.setattr(
        "mini_code_agent.worktrees.materialize._is_link_or_reparse",
        marks_src_as_link,
    )
    with pytest.raises(MaterializationError):
        materialize_index(
            root,
            (pointer("src/app.py"),),
            {"a" * 40: b"safe"},
            limits=WorktreeLimits(),
        )
