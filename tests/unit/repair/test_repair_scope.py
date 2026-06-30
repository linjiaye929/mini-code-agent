from __future__ import annotations

from pathlib import Path

import pytest

from mini_code_agent.policy.models import ActionPreview, RiskLevel
from mini_code_agent.repair.fingerprint import scope_sha256
from mini_code_agent.repair.scope import RepairActionGuard, RepairScope
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError


def test_scope_resolves_sorts_and_fingerprints_exact_files(tmp_path: Path) -> None:
    write_files(tmp_path, "src/b.py", "src/a.py")

    scope = RepairScope.create(
        WorkspaceBoundary(tmp_path),
        ("src/b.py", "src/a.py"),
    )

    assert scope.editable_paths == ("src/a.py", "src/b.py")
    assert scope.sha256 == scope_sha256(("src/a.py", "src/b.py"))


@pytest.mark.parametrize(
    "paths",
    (
        (),
        ("src/a.py", "src/a.py"),
        tuple(f"src/{index}.py" for index in range(33)),
    ),
)
def test_scope_rejects_invalid_cardinality(
    tmp_path: Path,
    paths: tuple[str, ...],
) -> None:
    write_files(tmp_path, *(set(paths) or {"src/a.py"}))

    with pytest.raises(ValueError):
        RepairScope.create(WorkspaceBoundary(tmp_path), paths)


@pytest.mark.parametrize("path", ("missing.py", "src"))
def test_scope_rejects_missing_or_directory_path(tmp_path: Path, path: str) -> None:
    (tmp_path / "src").mkdir()

    with pytest.raises(WorkspaceError):
        RepairScope.create(WorkspaceBoundary(tmp_path), (path,))


def test_scope_rejects_symlink_file(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink unavailable in this environment: {exc}")

    with pytest.raises(WorkspaceError):
        RepairScope.create(WorkspaceBoundary(tmp_path), ("link.py",))


@pytest.mark.parametrize(
    ("side_effect", "resources", "allowed"),
    (
        (SideEffect.READ_ONLY, (), True),
        (SideEffect.WRITE, ("src/a.py",), True),
        (SideEffect.WRITE, ("src/a.py", "src/b.py"), True),
        (SideEffect.WRITE, (), False),
        (SideEffect.WRITE, ("README.md",), False),
        (SideEffect.WRITE, ("src/a.py", "README.md"), False),
        (SideEffect.EXECUTE, ("src/a.py",), False),
        (SideEffect.NETWORK, (), False),
    ),
)
def test_action_guard_enforces_side_effect_and_exact_resources(
    tmp_path: Path,
    side_effect: SideEffect,
    resources: tuple[str, ...],
    allowed: bool,
) -> None:
    write_files(tmp_path, "src/a.py", "src/b.py")
    scope = RepairScope.create(
        WorkspaceBoundary(tmp_path),
        ("src/a.py", "src/b.py"),
    )

    result = RepairActionGuard(scope).evaluate(action(side_effect, resources))

    assert result.allowed is allowed
    assert len(result.public_message) <= 500


def action(
    side_effect: SideEffect,
    resources: tuple[str, ...] = (),
) -> ActionPreview:
    return ActionPreview(
        tool_call_id="call-1",
        tool_name="test_tool",
        side_effect=side_effect,
        risk=RiskLevel.LOW,
        summary="Test action.",
        resources=resources,
    )


def write_files(root: Path, *paths: str) -> None:
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("value = 1\n", encoding="utf-8")
