from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.subagents.models import SubagentProfile, SubagentStatus
from mini_code_agent.tools.edit_file import EditFileTool
from mini_code_agent.tools.write_file import WriteFileTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.worktrees.git import WorktreeGit
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import (
    CleanupStatus,
    SnapshotOutcome,
    SnapshotStatus,
    WorktreeProfile,
)
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore


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
    _git(repository, "config", "core.autocrlf", "true")
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
    cleanup = await manager.cleanup_lease(
        lease,
        SnapshotOutcome(
            lease_id=lease.lease_id,
            status=SnapshotStatus.NO_CHANGES,
        ),
    )
    assert cleanup.status is CleanupStatus.REMOVED


@pytest.mark.asyncio
async def test_real_lease_snapshot_persists_candidate_without_parent_mutation(
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
    (repository / "src").mkdir()
    parent_content = b"VALUE = 'base'\n"
    (repository / "src" / "app.py").write_bytes(parent_content)
    _git(repository, "add", "--", "src/app.py")
    _git(repository, "commit", "-m", "initial")
    profile = _profile(repository, state, Path(discovered_git).resolve(strict=True))
    store = WorktreeStateStore(profile)
    git = WorktreeGit(profile)
    manager = WorktreeManager(
        profile,
        git=git,
        store=store,
        id_factory=lambda: "lease-candidate",
    )
    lease = await manager.create_lease(child_id="child-candidate")
    workspace = WorkspaceBoundary(lease.worktree_path)
    ledger = MutationLedger(max_entries=8)
    before = workspace.read_text("src/app.py")
    edit_call = ToolCall(
        id="edit-1",
        name="edit_file",
        arguments={
            "path": "src/app.py",
            "old_text": "'base'",
            "new_text": "'changed'",
            "expected_sha256": before.sha256,
            "reason": "Update the value.",
        },
    )
    edit_result = await EditFileTool(workspace).execute(edit_call)
    ledger.record(edit_call, edit_result)
    write_call = ToolCall(
        id="write-1",
        name="write_file",
        arguments={
            "path": "src/new.py",
            "content": "NEW = True\n",
            "reason": "Add the requested module.",
        },
    )
    write_result = await WriteFileTool(workspace).execute(write_call)
    ledger.record(write_call, write_result)

    outcome = await CandidateSnapshotter(
        profile,
        store=store,
        blob_reader=git,
    ).snapshot(
        lease,
        ledger,
        candidate_id="candidate-real",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )

    assert outcome.status is SnapshotStatus.READY
    assert (repository / "src" / "app.py").read_bytes() == parent_content
    assert not (repository / "src" / "new.py").exists()
    assert (state / "candidates" / "ready" / "candidate-real" / "manifest.json").is_file()
    cleanup = await manager.cleanup_lease(lease, outcome)
    assert cleanup.status is CleanupStatus.REMOVED


def _profile(repository: Path, state: Path, git_executable: Path) -> WorktreeProfile:
    return WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=git_executable,
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


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )
