from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.subagents.models import SubagentProfile
from mini_code_agent.worktrees.git import WorktreeGit
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import WorktreeProfile


@pytest.mark.asyncio
async def test_real_no_checkout_lease_materializes_only_tracked_index(
    tmp_path: Path,
) -> None:
    discovered_git = shutil.which("git")
    if discovered_git is None:
        pytest.skip("Git is unavailable.")
    repository = tmp_path / "repository"
    state = tmp_path / "state"
    repository.mkdir()
    state.mkdir()
    if os.name != "nt":
        state.chmod(0o700)
    _git(repository, "init")
    _git(repository, "config", "user.email", "agent@example.invalid")
    _git(repository, "config", "user.name", "Agent Test")
    (repository / ".gitignore").write_text(".env\n.venv/\ncache/\n", encoding="utf-8")
    (repository / "src").mkdir()
    (repository / "src" / "app.py").write_bytes(b"print('tracked')\r\n")
    (repository / ".env").write_text("SECRET=ignored\n", encoding="utf-8")
    (repository / ".venv").mkdir()
    (repository / ".venv" / "token").write_text("ignored\n", encoding="utf-8")
    (repository / "cache").mkdir()
    (repository / "cache" / "data").write_text("ignored\n", encoding="utf-8")
    _git(repository, "add", "--", ".gitignore", "src/app.py")
    _git(repository, "commit", "-m", "initial")
    profile = WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=Path(discovered_git).resolve(strict=True),
        allowed_path_prefixes=("src", "tests"),
        implementation_profile=SubagentProfile(
            profile_id="implementation",
            local_name="delegate_implementation",
            description="Implement one bounded task.",
            system_prompt="Change only files required by the task.",
            tool_names=("read_file", "search_text", "write_file", "edit_file"),
            mode="implementation",
            agent_limits=AgentLimits(max_turns=8, max_tool_calls=32),
        ),
    )
    git = WorktreeGit(profile)
    manager = WorktreeManager(profile, git=git, id_factory=lambda: "lease-real")

    lease = await manager.create_lease(child_id="child-real")

    assert (lease.worktree_path / ".git").is_file()
    assert (lease.worktree_path / ".gitignore").is_file()
    assert (repository / "src" / "app.py").read_bytes() == b"print('tracked')\r\n"
    assert (lease.worktree_path / "src" / "app.py").read_bytes() == b"print('tracked')\n"
    assert not (lease.worktree_path / ".env").exists()
    assert not (lease.worktree_path / ".venv").exists()
    assert not (lease.worktree_path / "cache").exists()
    await git.unlock_worktree(lease.worktree_path)
    await git.remove_worktree(lease.worktree_path)
    await git.prune_worktrees()


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )
