from __future__ import annotations

import os
from pathlib import Path

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.subagents.models import SubagentProfile
from mini_code_agent.worktrees.models import WorktreeLimits, WorktreeProfile


def worktree_profile(
    tmp_path: Path,
    *,
    git_executable: Path | None = None,
    limits: WorktreeLimits | None = None,
) -> WorktreeProfile:
    repository = tmp_path / "repository"
    state = tmp_path / "state"
    executable = git_executable or tmp_path / ("git.exe" if os.name == "nt" else "git")
    repository.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    if git_executable is None:
        executable.touch(exist_ok=True)
    if os.name != "nt":
        state.chmod(0o700)
        if git_executable is None:
            executable.chmod(0o700)
    return WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=executable,
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
        limits=limits or WorktreeLimits(),
    )
