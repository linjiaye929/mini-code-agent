from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError, WorkspaceErrorCode
from mini_code_agent.workspace.models import WorkspaceLimits


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_preview_and_create_new_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    boundary = WorkspaceBoundary(tmp_path)

    preview = boundary.preview_write(
        "src/new.py",
        "print('new')\n",
        expected_sha256=None,
    )

    assert preview.path == "src/new.py"
    assert preview.created is True
    assert preview.before_sha256 is None
    assert preview.after_sha256 == sha256(b"print('new')\n")
    assert "--- a/src/new.py" in preview.diff
    assert "+++ b/src/new.py" in preview.diff
    assert str(tmp_path.resolve()) not in preview.model_dump_json()

    result = boundary.apply_write(
        "src/new.py",
        "print('new')\n",
        expected_sha256=None,
    )

    assert (tmp_path / "src" / "new.py").read_bytes() == b"print('new')\n"
    assert result.path == "src/new.py"
    assert result.created is True
    assert result.after_sha256 == preview.after_sha256


def test_replace_existing_file_requires_matching_hash(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_bytes(b"before\n")
    boundary = WorkspaceBoundary(tmp_path)
    before_hash = sha256(b"before\n")

    preview = boundary.preview_write(
        "app.py",
        "after\n",
        expected_sha256=before_hash,
    )
    result = boundary.apply_write(
        "app.py",
        "after\n",
        expected_sha256=before_hash,
    )

    assert preview.created is False
    assert preview.before_sha256 == before_hash
    assert result.before_sha256 == before_hash
    assert target.read_bytes() == b"after\n"


@pytest.mark.parametrize(
    ("exists", "expected_hash"),
    [
        (True, None),
        (True, "0" * 64),
        (False, "0" * 64),
    ],
)
def test_write_preconditions_fail_without_mutation(
    tmp_path: Path,
    exists: bool,
    expected_hash: str | None,
) -> None:
    target = tmp_path / "file.txt"
    if exists:
        target.write_bytes(b"original")
    boundary = WorkspaceBoundary(tmp_path)

    with pytest.raises(WorkspaceError) as captured:
        boundary.apply_write(
            "file.txt",
            "replacement",
            expected_sha256=expected_hash,
        )

    assert captured.value.code is WorkspaceErrorCode.CONFLICT
    assert target.read_bytes() == b"original" if exists else not target.exists()


def test_approved_preview_does_not_authorize_stale_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"before")
    boundary = WorkspaceBoundary(tmp_path)
    before_hash = sha256(b"before")
    boundary.preview_write(
        "file.txt",
        "after",
        expected_sha256=before_hash,
    )
    target.write_bytes(b"concurrent")

    with pytest.raises(WorkspaceError) as captured:
        boundary.apply_write(
            "file.txt",
            "after",
            expected_sha256=before_hash,
        )

    assert captured.value.code is WorkspaceErrorCode.CONFLICT
    assert target.read_bytes() == b"concurrent"


def test_write_rejects_noop_binary_invalid_encoding_and_size(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"same")
    boundary = WorkspaceBoundary(
        tmp_path,
        limits=WorkspaceLimits(max_write_bytes=5),
    )

    with pytest.raises(WorkspaceError) as noop:
        boundary.preview_write(
            "file.txt",
            "same",
            expected_sha256=sha256(b"same"),
        )
    with pytest.raises(WorkspaceError) as binary:
        boundary.preview_write("new.txt", "a\0b", expected_sha256=None)
    with pytest.raises(WorkspaceError) as encoding:
        boundary.preview_write("new.txt", "\ud800", expected_sha256=None)
    with pytest.raises(WorkspaceError) as too_large:
        boundary.preview_write("new.txt", "123456", expected_sha256=None)

    assert noop.value.code is WorkspaceErrorCode.CONFLICT
    assert binary.value.code is WorkspaceErrorCode.BINARY_FILE
    assert encoding.value.code is WorkspaceErrorCode.INVALID_ENCODING
    assert too_large.value.code is WorkspaceErrorCode.TOO_LARGE


def test_write_requires_existing_safe_parent(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)

    with pytest.raises(WorkspaceError) as missing:
        boundary.preview_write("missing/file.txt", "content", expected_sha256=None)
    with pytest.raises(WorkspaceError) as invalid:
        boundary.preview_write("../file.txt", "content", expected_sha256=None)

    assert missing.value.code is WorkspaceErrorCode.NOT_FOUND
    assert invalid.value.code is WorkspaceErrorCode.INVALID_PATH


def test_atomic_replace_preserves_mode_on_posix(tmp_path: Path) -> None:
    target = tmp_path / "script.sh"
    target.write_bytes(b"before\n")
    target.chmod(0o744)
    original_mode = target.stat().st_mode & 0o777
    boundary = WorkspaceBoundary(tmp_path)

    boundary.apply_write(
        "script.sh",
        "after\n",
        expected_sha256=sha256(b"before\n"),
    )

    assert target.stat().st_mode & 0o777 == original_mode


def test_diff_is_bounded(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_bytes(("before\n" * 100).encode())
    boundary = WorkspaceBoundary(
        tmp_path,
        limits=WorkspaceLimits(max_diff_chars=100),
    )

    preview = boundary.preview_write(
        "large.txt",
        "after\n" * 100,
        expected_sha256=sha256(target.read_bytes()),
    )

    assert len(preview.diff) <= 100
    assert preview.diff.endswith("\n... diff truncated ...\n")


def test_atomic_replace_failure_preserves_original_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"before")
    boundary = WorkspaceBoundary(tmp_path)

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("injected replace failure")

    monkeypatch.setattr(
        "mini_code_agent.workspace.boundary.os.replace",
        fail_replace,
    )

    with pytest.raises(WorkspaceError) as captured:
        boundary.apply_write(
            "file.txt",
            "after",
            expected_sha256=sha256(b"before"),
        )

    assert captured.value.code is WorkspaceErrorCode.WRITE_FAILED
    assert target.read_bytes() == b"before"
    assert list(tmp_path.glob(".mini-code-agent-*.tmp")) == []
    assert "injected replace failure" not in str(captured.value)
