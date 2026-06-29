from __future__ import annotations

import os
from pathlib import Path

import pytest

from mini_code_agent.checkpoint.models import CheckpointLimits, WorkspaceScanConfig
from mini_code_agent.checkpoint.workspace import (
    WorkspaceFingerprintError,
    WorkspaceFingerprintErrorCode,
    workspace_sha256,
)


def test_workspace_fingerprint_is_deterministic_and_binds_paths_and_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    nested = root / "src"
    nested.mkdir()
    (nested / "b.py").write_bytes(b"print('beta')\n")

    baseline = workspace_sha256(root)

    assert baseline == workspace_sha256(root)
    (nested / "b.py").write_bytes(b"print('changed')\n")
    assert baseline != workspace_sha256(root)
    (nested / "b.py").write_bytes(b"print('beta')\n")
    (root / "a.txt").rename(root / "renamed.txt")
    assert baseline != workspace_sha256(root)


def test_workspace_fingerprint_ignores_configured_directories(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "tracked.txt").write_text("stable", encoding="utf-8")
    ignored = root / ".git"
    ignored.mkdir()
    (ignored / "index").write_text("first", encoding="utf-8")

    baseline = workspace_sha256(root)
    (ignored / "index").write_text("second", encoding="utf-8")

    assert workspace_sha256(root) == baseline
    custom = WorkspaceScanConfig(excluded_directory_names=frozenset())
    assert workspace_sha256(root, config=custom) != baseline


def test_workspace_fingerprint_binds_scan_configuration(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    default = workspace_sha256(root)
    custom = workspace_sha256(
        root,
        config=WorkspaceScanConfig(excluded_directory_names=frozenset({"build"})),
    )

    assert default != custom


@pytest.mark.parametrize(
    ("limits", "files"),
    [
        (CheckpointLimits(max_workspace_files=1), {"a": b"1", "b": b"2"}),
        (CheckpointLimits(max_workspace_bytes=1_024), {"large": b"x" * 1_025}),
    ],
)
def test_workspace_fingerprint_enforces_scan_limits(
    tmp_path: Path,
    limits: CheckpointLimits,
    files: dict[str, bytes],
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for name, content in files.items():
        (root / name).write_bytes(content)

    with pytest.raises(WorkspaceFingerprintError) as captured:
        workspace_sha256(root, limits=limits)

    assert captured.value.code is WorkspaceFingerprintErrorCode.LIMIT_EXCEEDED
    assert str(root) not in str(captured.value)


def test_workspace_fingerprint_rejects_missing_or_file_root(tmp_path: Path) -> None:
    file_root = tmp_path / "file"
    file_root.write_text("content", encoding="utf-8")

    for root in (tmp_path / "missing", file_root):
        with pytest.raises(WorkspaceFingerprintError) as captured:
            workspace_sha256(root)
        assert captured.value.code is WorkspaceFingerprintErrorCode.UNAVAILABLE


def test_workspace_fingerprint_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "target"
    target.write_text("content", encoding="utf-8")
    link = root / "link"
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"Symlink unavailable in this environment: {exc}")

    with pytest.raises(WorkspaceFingerprintError) as captured:
        workspace_sha256(root)

    assert captured.value.code is WorkspaceFingerprintErrorCode.UNSAFE_ENTRY


def test_workspace_scan_config_is_immutable_and_validates_names() -> None:
    config = WorkspaceScanConfig()

    assert ".git" in config.excluded_directory_names
    with pytest.raises(ValueError):
        WorkspaceScanConfig(excluded_directory_names=frozenset({"../outside"}))
    with pytest.raises(ValueError):
        WorkspaceScanConfig(excluded_directory_names=frozenset({"nested/path"}))
