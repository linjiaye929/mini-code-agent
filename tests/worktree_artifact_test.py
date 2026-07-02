from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from mini_code_agent.agent.models import AgentLimits
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
from mini_code_agent.worktrees import (
    AdoptionStatus,
    CandidateAdoptionService,
    CandidateSnapshotter,
    DelegateImplementationTool,
    WorktreeFinalizer,
    WorktreeGit,
    WorktreeImplementationRunner,
    WorktreeManager,
    WorktreeProfile,
    WorktreeStateStore,
)


def _tool_response(call: ToolCall) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=(call,)),
        finish_reason=FinishReason.TOOL_CALL,
    )


def _stop_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
    )


class _ProviderFactory:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def create(self, profile: SubagentProfile, child_id: str) -> ModelProvider:
        assert profile.profile_id == "artifact-implementation"
        assert child_id == "artifact-child"
        return self._provider


class _ToolFactory:
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
                        id="allow-artifact-candidate-write",
                        decision=PolicyDecision.ALLOW,
                        rationale="The isolated artifact smoke permits bounded writes.",
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


async def verify_worktree_artifact() -> None:
    discovered_git = shutil.which("git")
    assert discovered_git is not None
    with tempfile.TemporaryDirectory(prefix="mini-code-agent-artifact-") as temporary:
        root = Path(temporary)
        repository = root / "repository"
        state = root / "state"
        repository.mkdir()
        state.mkdir()
        if os.name != "nt":
            state.chmod(0o700)
        _git(repository, "init")
        _git(repository, "config", "user.email", "artifact@example.invalid")
        _git(repository, "config", "user.name", "Artifact Smoke")
        (repository / "src").mkdir()
        before = b"VALUE = 'base'\n"
        (repository / "src" / "app.py").write_bytes(before)
        _git(repository, "add", "--", "src/app.py")
        _git(repository, "commit", "-m", "initial")

        profile = WorktreeProfile(
            repository_root=repository,
            state_root=state,
            git_executable=Path(discovered_git).resolve(strict=True),
            allowed_path_prefixes=("src",),
            implementation_profile=SubagentProfile(
                profile_id="artifact-implementation",
                local_name="delegate_implementation",
                description="Implement one bounded artifact smoke change.",
                system_prompt="Use only the lease Tools.",
                tool_names=("read_file", "search_text", "write_file", "edit_file"),
                mode="implementation",
                agent_limits=AgentLimits(
                    max_turns=6,
                    max_tool_calls=8,
                    provider_timeout_seconds=5,
                    tool_timeout_seconds=5,
                ),
                limits=SubagentLimits(
                    max_tasks=1,
                    max_concurrency=1,
                    max_task_chars=1_000,
                    child_timeout_seconds=15,
                    batch_timeout_seconds=15,
                    max_summary_chars=1_000,
                    max_evidence_items=8,
                    max_result_bytes=128_000,
                ),
            ),
        )
        child_provider = ScriptedProvider(
            (
                _tool_response(
                    ToolCall(
                        id="artifact-edit",
                        name="edit_file",
                        arguments={
                            "path": "src/app.py",
                            "old_text": "'base'",
                            "new_text": "'adopted'",
                            "expected_sha256": hashlib.sha256(before).hexdigest(),
                            "reason": "Exercise candidate modification.",
                        },
                    )
                ),
                _tool_response(
                    ToolCall(
                        id="artifact-write",
                        name="write_file",
                        arguments={
                            "path": "src/new.py",
                            "content": "NEW = True\n",
                            "reason": "Exercise candidate addition.",
                        },
                    )
                ),
                _stop_response("Implementation complete."),
            )
        )
        store = WorktreeStateStore(profile)
        git = WorktreeGit(profile)
        manager = WorktreeManager(
            profile,
            git=git,
            store=store,
            id_factory=lambda: "artifact-lease",
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
            provider_factory=_ProviderFactory(child_provider),
            tool_factory=_ToolFactory(),
            id_factory=iter(("artifact-child", "artifact-candidate")).__next__,
        )
        delegated = await DelegateImplementationTool(runner).execute(
            ToolCall(
                id="artifact-delegation",
                name="delegate_implementation",
                arguments={
                    "task": "Change VALUE and add src/new.py.",
                    "reason": "Verify the installed Worktree implementation flow.",
                },
            )
        )
        payload = json.loads(delegated.content)
        assert delegated.is_error is False
        assert payload["snapshot_status"] == "ready"
        assert payload["cleanup_status"] == "removed"
        assert payload["candidate"]["candidate_id"] == "artifact-candidate"
        assert (repository / "src" / "app.py").read_bytes() == before
        assert not (repository / "src" / "new.py").exists()
        assert _git_output(repository, "status", "--porcelain") == b""

        service = CandidateAdoptionService(profile, store=store, git=git)
        await service.preview("artifact-candidate")
        adopted = await service.adopt("artifact-candidate")
        assert adopted.status is AdoptionStatus.APPLIED
        assert (repository / "src" / "app.py").read_bytes() == b"VALUE = 'adopted'\n"
        assert (repository / "src" / "new.py").read_bytes() == b"NEW = True\n"
        assert await git.changed_paths() == ("src/app.py", "src/new.py")


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


if __name__ == "__main__":
    asyncio.run(verify_worktree_artifact())
