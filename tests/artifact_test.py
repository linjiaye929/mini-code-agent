from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

FORBIDDEN_ROOTS = {
    ".git",
    ".idea",
    ".mini-code-agent",
    ".pytest_cache",
    ".pyright",
    ".ruff_cache",
    ".venv",
    ".vscode",
    ".worktrees",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
}
FORBIDDEN_FILES = {".coverage", ".env"}


def _project_relative(member: str) -> str:
    normalized = member.replace("\\", "/").lstrip("./")
    first, separator, remainder = normalized.partition("/")
    if separator and first.startswith("mini_code_agent-"):
        return remainder
    return normalized


def _forbidden_member(member: str) -> bool:
    relative = _project_relative(member)
    parts = tuple(part for part in relative.split("/") if part)
    return bool(
        parts
        and (parts[0] in FORBIDDEN_ROOTS or parts[-1] in FORBIDDEN_FILES or "__pycache__" in parts)
    )


def _archive_members(path: Path) -> tuple[str, ...]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, mode="r:gz") as archive:
            return tuple(member.name for member in archive.getmembers())
    raise AssertionError(f"Unsupported release artifact: {path.name}")


def verify_release_artifacts(dist: Path) -> None:
    artifacts = sorted((*dist.glob("*.whl"), *dist.glob("*.tar.gz")))
    assert {path.suffix for path in artifacts} == {".gz", ".whl"}
    for artifact in artifacts:
        forbidden = sorted(
            member for member in _archive_members(artifact) if _forbidden_member(member)
        )
        assert forbidden == [], f"{artifact.name} contains local state: {forbidden}"


if __name__ == "__main__":
    verify_release_artifacts(Path(__file__).resolve().parents[1] / "dist")
