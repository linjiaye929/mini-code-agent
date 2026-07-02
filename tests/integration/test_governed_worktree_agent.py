from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import (
    PolicyDecision,
    PolicyRule,
    SessionMode,
    TrustSource,
)
from mini_code_agent.providers.base import FinishReason, ModelProvider, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.subagents.models import SubagentLimits, SubagentProfile
from mini_code_agent.tools.base import SideEffect, ToolExecutor
from mini_code_agent.tools.edit_file import EditFileTool
from mini_code_agent.tools.read_file import ReadFileTool
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.search_text import SearchTextTool
from mini_code_agent.tools.write_file import WriteFileTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.worktrees.finalization import WorktreeFinalizer
from mini_code_agent.worktrees.git import WorktreeGit
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import WorktreeProfile
from mini_code_agent.worktrees.runner import WorktreeImplementationRunner
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore
from mini_code_agent.worktrees.tools import build_worktree_tools


def tool_response(call: ToolCall) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=(call,)),
        finish_reason=FinishReason.TOOL_CALL,
    )


def stop_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
    )


class OneProviderFactory:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        self.calls: list[tuple[str, str]] = []

    def create(self, profile: SubagentProfile, child_id: str) -> ModelProvider:
        self.calls.append((profile.profile_id, child_id))
        return self.provider


class RealImplementationToolFactory:
    def create(
        self,
        profile: SubagentProfile,
        workspace: WorkspaceBoundary,
    ) -> ToolExecutor:
        executor = GovernedToolExecutor(
            ToolRegistry(
                (
                    ReadFileTool(workspace),
                    SearchTextTool(workspace),
                    WriteFileTool(workspace),
                    EditFileTool(workspace),
                )
            ),
            policy=PolicyEngine(
                (
                    PolicyRule(
                        id="allow-subagent-write",
                        decision=PolicyDecision.ALLOW,
                        rationale="The isolated implementation profile permits CAS writes.",
                        side_effect=SideEffect.WRITE,
                        trust_source=TrustSource.SUBAGENT,
                    ),
                )
            ),
            approval=StaticApprovalHandler(approved=False),
            session_mode=SessionMode.NON_INTERACTIVE,
            trust_source=TrustSource.SUBAGENT,
        )
        assert tuple(item.name for item in executor.definitions) == profile.tool_names
        return executor


@pytest.mark.asyncio
async def test_parent_delegates_real_child_and_receives_ready_candidate(
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
    profile = _profile(
        repository,
        state,
        Path(discovered_git).resolve(strict=True),
    )
    before_sha256 = hashlib.sha256(parent_content).hexdigest()
    child_provider = ScriptedProvider(
        (
            tool_response(
                ToolCall(
                    id="edit-1",
                    name="edit_file",
                    arguments={
                        "path": "src/app.py",
                        "old_text": "'base'",
                        "new_text": "'changed'",
                        "expected_sha256": before_sha256,
                        "reason": "Implement the requested value change.",
                    },
                )
            ),
            tool_response(
                ToolCall(
                    id="write-1",
                    name="write_file",
                    arguments={
                        "path": "src/new.py",
                        "content": "NEW = True\n",
                        "reason": "Add the requested module.",
                    },
                )
            ),
            stop_response("CHILD_SECRET_SUMMARY"),
        )
    )
    provider_factory = OneProviderFactory(child_provider)
    store = WorktreeStateStore(profile)
    git = WorktreeGit(profile)
    manager = WorktreeManager(
        profile,
        git=git,
        store=store,
        id_factory=lambda: "lease-real",
    )
    runner = WorktreeImplementationRunner(
        profile,
        manager=manager,
        finalizer=WorktreeFinalizer(
            snapshotter=CandidateSnapshotter(
                profile,
                store=store,
                blob_reader=git,
            ),
            cleaner=manager,
        ),
        provider_factory=provider_factory,
        tool_factory=RealImplementationToolFactory(),
        id_factory=iter(("child-real", "candidate-real")).__next__,
    )
    parent_provider = ScriptedProvider(
        (
            tool_response(
                ToolCall(
                    id="delegate-1",
                    name="delegate_implementation",
                    arguments={
                        "task": "Change VALUE and add src/new.py.",
                        "reason": "Implement the bounded change in isolation.",
                    },
                )
            ),
            stop_response("Parent received the candidate."),
        )
    )
    parent_tools = GovernedToolExecutor(
        ToolRegistry(build_worktree_tools((runner,))),
        policy=PolicyEngine(
            (
                PolicyRule(
                    id="allow-implementation-delegation",
                    decision=PolicyDecision.ALLOW,
                    rationale="The host explicitly enables isolated implementation.",
                    tool_glob="delegate_implementation",
                    side_effect=SideEffect.EXECUTE,
                    trust_source=TrustSource.MODEL,
                ),
            )
        ),
        approval=StaticApprovalHandler(approved=False),
        session_mode=SessionMode.NON_INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )

    result = await AgentRuntime(
        parent_provider,
        parent_tools,
        limits=AgentLimits(max_turns=4, max_tool_calls=2),
    ).run(user_prompt="Delegate the implementation.")

    assert result.stop_reason is StopReason.COMPLETED
    assert provider_factory.calls == [("implementation", "child-real")]
    payload = _delegated_payload(parent_provider)
    assert payload["content_type"] == "governed_worktree_result"
    assert payload["snapshot_status"] == "ready"
    assert payload["cleanup_status"] == "removed"
    candidate = cast(dict[str, object], payload["candidate"])
    assert candidate["candidate_id"] == "candidate-real"
    assert candidate["changed_files"] == 2
    serialized = json.dumps(payload, sort_keys=True)
    assert "Change VALUE" not in serialized
    assert "CHILD_SECRET_SUMMARY" not in serialized
    assert "NEW = True" not in serialized
    assert (repository / "src" / "app.py").read_bytes() == parent_content
    assert not (repository / "src" / "new.py").exists()
    assert _git_output(repository, "status", "--porcelain") == b""
    assert not (state / "leases" / "lease-real").exists()
    assert (state / "candidates" / "ready" / "candidate-real" / "manifest.json").is_file()


def _profile(
    repository: Path,
    state: Path,
    git_executable: Path,
) -> WorktreeProfile:
    return WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=git_executable,
        allowed_path_prefixes=("src", "tests"),
        implementation_profile=SubagentProfile(
            profile_id="implementation",
            local_name="delegate_implementation",
            description="Implement one bounded task in an isolated Worktree.",
            system_prompt="Use only the lease Tools and implement the assigned task.",
            tool_names=("read_file", "search_text", "write_file", "edit_file"),
            mode="implementation",
            agent_limits=AgentLimits(
                max_turns=6,
                max_tool_calls=8,
                provider_timeout_seconds=2,
                tool_timeout_seconds=2,
            ),
            limits=SubagentLimits(
                max_tasks=1,
                max_concurrency=1,
                max_task_chars=1_000,
                child_timeout_seconds=5,
                batch_timeout_seconds=5,
                max_summary_chars=1_000,
                max_evidence_items=8,
                max_result_bytes=128_000,
            ),
        ),
    )


def _delegated_payload(provider: ScriptedProvider) -> dict[str, object]:
    message = provider.requests[1].messages[-1]
    result = message.tool_results[0]
    return cast(dict[str, object], json.loads(result.content))


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )


def _git_output(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    ).stdout
