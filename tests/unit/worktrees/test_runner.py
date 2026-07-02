from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import (
    ActionPreview,
    PolicyDecision,
    PolicyRule,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.providers.base import FinishReason, ModelProvider, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.models import SubagentLimits, SubagentProfile, SubagentStatus
from mini_code_agent.tools.base import SideEffect, ToolDefinition, ToolExecutor
from mini_code_agent.tools.edit_file import EditFileTool
from mini_code_agent.tools.read_file import ReadFileTool
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.search_text import SearchTextTool
from mini_code_agent.tools.write_file import WriteFileTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.worktrees.finalization import WorktreeFinalizer
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import SnapshotStatus, WorktreeProfile
from mini_code_agent.worktrees.runner import WorktreeImplementationRunner
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore
from mini_code_agent.worktrees.tools import DelegateImplementationTool

from .helpers import worktree_profile
from .test_manager_leases import FakeGit


def stop_provider(*, delay_seconds: float = 0) -> ScriptedProvider:
    return ScriptedProvider(
        (
            ModelResponse(
                message=Message.assistant_text("UNTRUSTED_CHILD_SUMMARY"),
                finish_reason=FinishReason.STOP,
            ),
        ),
        delay_seconds=delay_seconds,
    )


def tool_response(call: ToolCall) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=(call,)),
        finish_reason=FinishReason.TOOL_CALL,
    )


class ProviderFactory:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        self.calls: list[tuple[str, str]] = []

    def create(self, profile: SubagentProfile, child_id: str) -> ModelProvider:
        self.calls.append((profile.profile_id, child_id))
        return self.provider


class ToolFactory:
    def create(
        self,
        profile: SubagentProfile,
        workspace: WorkspaceBoundary,
    ) -> ToolExecutor:
        del profile
        return GovernedToolExecutor(
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
                        id="allow-isolated-write",
                        decision=PolicyDecision.ALLOW,
                        rationale="Allow CAS writes in the isolated lease.",
                        side_effect=SideEffect.WRITE,
                        trust_source=TrustSource.SUBAGENT,
                    ),
                )
            ),
            approval=StaticApprovalHandler(approved=False),
            session_mode=SessionMode.NON_INTERACTIVE,
            trust_source=TrustSource.SUBAGENT,
        )


class FailingToolFactory:
    def create(
        self,
        profile: SubagentProfile,
        workspace: WorkspaceBoundary,
    ) -> ToolExecutor:
        del profile, workspace
        raise RuntimeError("secret factory failure")


class MutatingTestTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="run_tests",
        description="Run a fixed test profile.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect=SideEffect.EXECUTE,
    )

    def __init__(self, workspace: WorkspaceBoundary) -> None:
        self._workspace = workspace

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=call.name,
            side_effect=SideEffect.EXECUTE,
            risk=RiskLevel.CRITICAL,
            summary="Run the fixed test profile.",
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        (self._workspace.root / "src" / "test-output.py").write_text(
            "OUT_OF_BAND = True\n",
            encoding="utf-8",
        )
        return ToolResult(tool_call_id=call.id, content='{"passed":true}')


class MutatingTestToolFactory:
    def create(
        self,
        profile: SubagentProfile,
        workspace: WorkspaceBoundary,
    ) -> ToolExecutor:
        del profile
        return GovernedToolExecutor(
            ToolRegistry(
                (
                    ReadFileTool(workspace),
                    SearchTextTool(workspace),
                    WriteFileTool(workspace),
                    EditFileTool(workspace),
                    MutatingTestTool(workspace),
                )
            ),
            policy=PolicyEngine(
                (
                    PolicyRule(
                        id="allow-fixed-tests",
                        decision=PolicyDecision.ALLOW,
                        rationale="Allow the fixed isolated test profile.",
                        side_effect=SideEffect.EXECUTE,
                        trust_source=TrustSource.SUBAGENT,
                    ),
                )
            ),
            approval=StaticApprovalHandler(approved=False),
            session_mode=SessionMode.NON_INTERACTIVE,
            trust_source=TrustSource.SUBAGENT,
        )


def runner_for(
    tmp_path: Path,
    *,
    provider: ModelProvider,
    profile: WorktreeProfile | None = None,
    tool_factory: object | None = None,
    ids: tuple[str, str] = ("child-1", "candidate-1"),
):
    active_profile = profile or worktree_profile(tmp_path)
    store = WorktreeStateStore(active_profile)
    git = FakeGit(active_profile.repository_root)
    manager = WorktreeManager(
        active_profile,
        git=git,
        store=store,
        id_factory=lambda: "lease-1",
    )
    factory = ProviderFactory(provider)
    iterator = iter(ids)
    runner = WorktreeImplementationRunner(
        active_profile,
        manager=manager,
        finalizer=WorktreeFinalizer(
            snapshotter=CandidateSnapshotter(
                active_profile,
                store=store,
                blob_reader=git,
            ),
            cleaner=manager,
        ),
        provider_factory=factory,
        tool_factory=tool_factory or ToolFactory(),  # type: ignore[arg-type]
        id_factory=iterator.__next__,
    )
    return active_profile, git, factory, runner


@pytest.mark.asyncio
async def test_runner_completes_no_change_child_and_removes_lease(tmp_path: Path) -> None:
    profile, _, provider_factory, runner = runner_for(
        tmp_path,
        provider=stop_provider(),
    )

    result = await runner.run(
        parent_tool_call_id="delegate-1",
        task="Inspect and make no unnecessary changes.",
    )

    assert result.child.status is SubagentStatus.COMPLETED
    assert result.finalization.snapshot.status is SnapshotStatus.NO_CHANGES
    assert provider_factory.calls == [("implementation", "child-1")]
    assert not (profile.state_root / "leases" / "lease-1").exists()


@pytest.mark.asyncio
async def test_runner_snapshots_timeout_then_removes_clean_lease(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    implementation = profile.implementation_profile
    limits = SubagentLimits.model_validate(
        implementation.limits.model_dump()
        | {
            "child_timeout_seconds": 0.01,
            "batch_timeout_seconds": 0.02,
        }
    )
    timed_profile = WorktreeProfile.model_validate(
        profile.model_dump()
        | {"implementation_profile": implementation.model_copy(update={"limits": limits})}
    )
    _, _, _, runner = runner_for(
        tmp_path,
        profile=timed_profile,
        provider=stop_provider(delay_seconds=1),
    )

    result = await runner.run(
        parent_tool_call_id="delegate-1",
        task="Time out safely.",
    )

    assert result.child.status is SubagentStatus.TIMED_OUT
    assert result.finalization.snapshot.status is SnapshotStatus.NO_CHANGES
    assert not (timed_profile.state_root / "leases" / "lease-1").exists()


@pytest.mark.asyncio
async def test_runner_cleans_lease_after_composition_failure(tmp_path: Path) -> None:
    profile, _, provider_factory, runner = runner_for(
        tmp_path,
        provider=stop_provider(),
        tool_factory=FailingToolFactory(),
    )

    with pytest.raises(SubagentCompositionError):
        await runner.run(
            parent_tool_call_id="delegate-1",
            task="Fail composition safely.",
        )

    assert provider_factory.calls == []
    assert not (profile.state_root / "leases" / "lease-1").exists()


@pytest.mark.asyncio
async def test_runner_rejects_duplicate_ids_before_lease_or_provider(
    tmp_path: Path,
) -> None:
    profile, _, provider_factory, runner = runner_for(
        tmp_path,
        provider=stop_provider(),
        ids=("duplicate", "duplicate"),
    )

    with pytest.raises(SubagentCompositionError):
        await runner.run(
            parent_tool_call_id="delegate-1",
            task="Reject duplicate IDs.",
        )

    assert provider_factory.calls == []
    assert not (profile.state_root / "leases").exists()


@pytest.mark.asyncio
async def test_runner_persists_test_created_out_of_band_change_as_rejected(
    tmp_path: Path,
) -> None:
    profile = worktree_profile(tmp_path)
    implementation = profile.implementation_profile.model_copy(
        update={
            "tool_names": (
                "read_file",
                "search_text",
                "write_file",
                "edit_file",
                "run_tests",
            )
        }
    )
    test_profile = WorktreeProfile.model_validate(
        profile.model_dump() | {"implementation_profile": implementation}
    )
    provider = ScriptedProvider(
        (
            tool_response(ToolCall(id="tests-1", name="run_tests", arguments={})),
            ModelResponse(
                message=Message.assistant_text("Tests completed."),
                finish_reason=FinishReason.STOP,
            ),
        )
    )
    _, _, _, runner = runner_for(
        tmp_path,
        profile=test_profile,
        provider=provider,
        tool_factory=MutatingTestToolFactory(),
    )

    result = await runner.run(
        parent_tool_call_id="delegate-1",
        task="Run the fixed tests.",
    )

    assert result.finalization.snapshot.status is SnapshotStatus.REJECTED
    manifest = result.finalization.snapshot.manifest
    assert manifest is not None
    assert "ledger_mismatch" in manifest.rejection_reasons
    assert (
        test_profile.state_root / "candidates" / "rejected" / "candidate-1" / "manifest.json"
    ).is_file()
    assert not (test_profile.state_root / "leases" / "lease-1").exists()


@pytest.mark.asyncio
async def test_delegate_tool_strict_arguments_and_bounded_projection(
    tmp_path: Path,
) -> None:
    _, _, _, runner = runner_for(tmp_path, provider=stop_provider())
    tool = DelegateImplementationTool(runner)
    invalid = await tool.execute(
        ToolCall(
            id="delegate-invalid",
            name="delegate_implementation",
            arguments={"task": "Missing reason."},
        )
    )
    preview_call = ToolCall(
        id="delegate-1",
        name="delegate_implementation",
        arguments={"task": "Make no changes.", "reason": "Bounded test."},
    )

    preview = await tool.preview(preview_call)
    result = await tool.execute(preview_call)
    payload = json.loads(result.content)

    assert invalid.is_error is True
    assert preview.side_effect is SideEffect.EXECUTE
    assert result.is_error is False
    assert payload["content_type"] == "governed_worktree_result"
    assert payload["snapshot_status"] == "no_changes"
    assert "UNTRUSTED_CHILD_SUMMARY" not in result.content
    assert "Make no changes" not in result.content
