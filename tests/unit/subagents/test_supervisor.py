from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import SessionMode, TrustSource
from mini_code_agent.providers.base import FinishReason, ModelProvider, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.events import (
    RecordingSubagentEventSink,
    SubagentBatchCompleted,
    SubagentBatchStarted,
    SubagentCompleted,
    SubagentStarted,
)
from mini_code_agent.subagents.models import (
    SubagentLimits,
    SubagentProfile,
    SubagentStatus,
)
from mini_code_agent.subagents.supervisor import SubagentSupervisor
from mini_code_agent.tools.base import SideEffect, ToolDefinition, ToolExecutor
from mini_code_agent.tools.registry import ToolRegistry


class ReadOnlyTool:
    def __init__(self, name: str) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=f"Test {name}.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            side_effect=SideEffect.READ_ONLY,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, content=f"{call.name} result")


def governed_tools(
    *,
    trust_source: TrustSource = TrustSource.SUBAGENT,
) -> GovernedToolExecutor:
    return GovernedToolExecutor(
        ToolRegistry((ReadOnlyTool("read_file"), ReadOnlyTool("search_text"))),
        policy=PolicyEngine(),
        approval=StaticApprovalHandler(approved=False),
        session_mode=SessionMode.NON_INTERACTIVE,
        trust_source=trust_source,
    )


def final_response(
    text: str = "review complete",
    *,
    finish_reason: FinishReason = FinishReason.STOP,
) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=finish_reason,
    )


class ProviderFactory:
    def __init__(
        self,
        providers: tuple[ModelProvider, ...],
        *,
        error: Exception | None = None,
    ) -> None:
        self.providers = deque(providers)
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def create(
        self,
        profile: SubagentProfile,
        child_id: str,
    ) -> ModelProvider:
        self.calls.append((profile.profile_id, child_id))
        if self.error is not None:
            raise self.error
        return self.providers.popleft()


class ToolFactory:
    def __init__(
        self,
        tools: tuple[ToolExecutor, ...],
        *,
        error: Exception | None = None,
    ) -> None:
        self.tools = deque(tools)
        self.error = error
        self.calls: list[tuple[str, Path]] = []

    def create(
        self,
        profile: SubagentProfile,
        workspace_root: Path,
    ) -> ToolExecutor:
        self.calls.append((profile.profile_id, workspace_root))
        if self.error is not None:
            raise self.error
        return self.tools.popleft()


def profile_for(**limit_changes: object) -> SubagentProfile:
    limits: dict[str, object] = {
        "max_tasks": 4,
        "max_concurrency": 2,
        "max_evidence_items": 8,
        "child_timeout_seconds": 1,
        "batch_timeout_seconds": 3,
    }
    limits.update(limit_changes)
    return SubagentProfile(
        profile_id="review",
        local_name="delegate_analysis",
        description="Run isolated review.",
        system_prompt="Review only the assigned task.",
        tool_names=("read_file", "search_text"),
        agent_limits=AgentLimits(
            max_turns=4,
            max_tool_calls=8,
            provider_timeout_seconds=1,
            tool_timeout_seconds=1,
        ),
        limits=SubagentLimits.model_validate(limits),
    )


def supervisor_for(
    tmp_path: Path,
    *,
    providers: tuple[ModelProvider, ...],
    tools: tuple[ToolExecutor, ...] | None = None,
    events: RecordingSubagentEventSink | None = None,
    child_ids: tuple[str, ...] = ("child-1",),
    profile: SubagentProfile | None = None,
) -> tuple[SubagentSupervisor, ProviderFactory, ToolFactory]:
    provider_factory = ProviderFactory(providers)
    tool_factory = ToolFactory(
        tools or tuple(governed_tools() for _ in providers)
    )
    ids = iter(child_ids)
    supervisor = SubagentSupervisor(
        profile or profile_for(),
        workspace_root=tmp_path,
        provider_factory=provider_factory,
        tool_factory=tool_factory,
        events=events,
        id_factory=lambda: next(ids),
    )
    return supervisor, provider_factory, tool_factory


@pytest.mark.asyncio
async def test_one_child_gets_fresh_context_exact_tools_and_bounded_result(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider((final_response(),))
    events = RecordingSubagentEventSink()
    supervisor, provider_factory, tool_factory = supervisor_for(
        tmp_path,
        providers=(provider,),
        events=events,
    )

    batch = await supervisor.run_batch(
        parent_tool_call_id="parent-1",
        tasks=("Inspect parser bounds.",),
    )

    assert provider.requests[0].system_prompt == supervisor.profile.system_prompt
    assert provider.requests[0].messages == (
        Message.user_text("Inspect parser bounds."),
    )
    assert tuple(item.name for item in provider.requests[0].tools) == (
        "read_file",
        "search_text",
    )
    assert batch.children[0].untrusted_summary == "review complete"
    assert batch.children[0].status is SubagentStatus.COMPLETED
    assert batch.children[0].stop_reason is StopReason.COMPLETED
    assert batch.completed == 1
    assert provider_factory.calls == [("review", "child-1")]
    assert tool_factory.calls == [("review", tmp_path.resolve())]
    assert [type(item) for item in events.events] == [
        SubagentBatchStarted,
        SubagentStarted,
        SubagentCompleted,
        SubagentBatchCompleted,
    ]


@pytest.mark.asyncio
async def test_non_completed_agent_result_is_stopped_not_failed(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        (final_response("limit reached", finish_reason=FinishReason.MAX_TOKENS),)
    )
    supervisor, _, _ = supervisor_for(tmp_path, providers=(provider,))

    batch = await supervisor.run_batch(
        parent_tool_call_id="parent-1",
        tasks=("Inspect parser.",),
    )

    child = batch.children[0]
    assert child.status is SubagentStatus.STOPPED
    assert child.stop_reason is StopReason.PROVIDER_LIMIT
    assert batch.stopped == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_name", ["provider", "tools"])
async def test_factory_failure_is_static_and_starts_no_provider(
    tmp_path: Path,
    factory_name: str,
) -> None:
    provider = ScriptedProvider((final_response(),))
    provider_factory = ProviderFactory(
        (provider,),
        error=RuntimeError("secret provider") if factory_name == "provider" else None,
    )
    tool_factory = ToolFactory(
        (governed_tools(),),
        error=RuntimeError("secret tools") if factory_name == "tools" else None,
    )
    events = RecordingSubagentEventSink()
    supervisor = SubagentSupervisor(
        profile_for(),
        workspace_root=tmp_path,
        provider_factory=provider_factory,
        tool_factory=tool_factory,
        events=events,
        id_factory=lambda: "child-1",
    )

    with pytest.raises(SubagentCompositionError) as caught:
        await supervisor.run_batch(
            parent_tool_call_id="parent-1",
            tasks=("Inspect parser.",),
        )

    assert str(caught.value) == "Subagent capabilities did not match the host profile."
    assert provider.requests == []
    assert events.events == []


@pytest.mark.asyncio
async def test_all_children_are_composed_before_any_provider_call(
    tmp_path: Path,
) -> None:
    shared = ScriptedProvider((final_response(), final_response()))
    supervisor, _, _ = supervisor_for(
        tmp_path,
        providers=(shared, shared),
        child_ids=("child-1", "child-2"),
    )

    with pytest.raises(SubagentCompositionError):
        await supervisor.run_batch(
            parent_tool_call_id="parent-1",
            tasks=("one", "two"),
        )

    assert shared.requests == []


@pytest.mark.asyncio
async def test_event_sink_failure_does_not_replace_child_result(
    tmp_path: Path,
) -> None:
    class FailingSink:
        def publish(self, event: object) -> None:
            del event
            raise RuntimeError("sink failed")

    provider = ScriptedProvider((final_response(),))
    provider_factory = ProviderFactory((provider,))
    tool_factory = ToolFactory((governed_tools(),))
    supervisor = SubagentSupervisor(
        profile_for(),
        workspace_root=tmp_path,
        provider_factory=provider_factory,
        tool_factory=tool_factory,
        events=FailingSink(),  # type: ignore[arg-type]
        id_factory=lambda: "child-1",
    )

    batch = await supervisor.run_batch(
        parent_tool_call_id="parent-1",
        tasks=("Inspect parser.",),
    )

    assert batch.completed == 1


def test_supervisor_rejects_invalid_workspace_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(ValueError):
        SubagentSupervisor(
            profile_for(),
            workspace_root=missing,
            provider_factory=ProviderFactory(()),
            tool_factory=ToolFactory(()),
        )
