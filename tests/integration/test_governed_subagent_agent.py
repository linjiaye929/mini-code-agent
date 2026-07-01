from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
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
from mini_code_agent.providers.base import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderStreamEvent,
    ResponseCompleted,
)
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.subagents.events import RecordingSubagentEventSink
from mini_code_agent.subagents.models import (
    SubagentLimits,
    SubagentProfile,
)
from mini_code_agent.subagents.supervisor import SubagentSupervisor
from mini_code_agent.subagents.tools import build_subagent_tools
from mini_code_agent.tools.base import ToolExecutor
from mini_code_agent.tools.read_file import ReadFileTool
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.search_text import SearchTextTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary


def profile_for(
    *,
    child_timeout_seconds: float = 1,
    batch_timeout_seconds: float = 3,
) -> SubagentProfile:
    return SubagentProfile(
        profile_id="review",
        local_name="delegate_analysis",
        description="Delegate isolated read-only code analysis.",
        system_prompt="Use only the assigned read-only tools and return a brief summary.",
        tool_names=("read_file", "search_text"),
        agent_limits=AgentLimits(
            max_turns=4,
            max_tool_calls=4,
            provider_timeout_seconds=1,
            tool_timeout_seconds=1,
        ),
        limits=SubagentLimits(
            max_tasks=4,
            max_concurrency=2,
            max_task_chars=1_000,
            child_timeout_seconds=child_timeout_seconds,
            batch_timeout_seconds=batch_timeout_seconds,
            max_summary_chars=1_000,
            max_evidence_items=4,
            max_result_bytes=64_000,
        ),
    )


def tool_response(call: ToolCall) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=(call,),
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


def stop_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
    )


def child_read_provider(summary: str = "Read review complete.") -> ScriptedProvider:
    return ScriptedProvider(
        (
            tool_response(
                ToolCall(
                    id="read-1",
                    name="read_file",
                    arguments={"path": "src/app.py"},
                )
            ),
            stop_response(summary),
        )
    )


def child_search_provider(
    summary: str = "Search review complete.",
) -> ScriptedProvider:
    return ScriptedProvider(
        (
            tool_response(
                ToolCall(
                    id="search-1",
                    name="search_text",
                    arguments={"query": "needle", "glob": "*.py"},
                )
            ),
            stop_response(summary),
        )
    )


def parent_provider_for(
    tasks: tuple[str, ...],
    *,
    final_text: str = "Delegation complete.",
) -> ScriptedProvider:
    return ScriptedProvider(
        (
            tool_response(
                ToolCall(
                    id="delegate-1",
                    name="delegate_analysis",
                    arguments={
                        "tasks": list(tasks),
                        "reason": "Independent bounded review.",
                    },
                )
            ),
            stop_response(final_text),
        )
    )


class RecordingProviderFactory:
    def __init__(self, providers: tuple[ModelProvider, ...]) -> None:
        self._providers = deque(providers)
        self.calls: list[tuple[str, str]] = []

    def create(
        self,
        profile: SubagentProfile,
        child_id: str,
    ) -> ModelProvider:
        self.calls.append((profile.profile_id, child_id))
        return self._providers.popleft()


class RealReadOnlyToolFactory:
    def __init__(self) -> None:
        self.executors: list[GovernedToolExecutor] = []

    def create(
        self,
        profile: SubagentProfile,
        workspace_root: Path,
    ) -> ToolExecutor:
        workspace = WorkspaceBoundary(workspace_root)
        registry = ToolRegistry(
            (
                ReadFileTool(workspace),
                SearchTextTool(workspace),
            )
        )
        executor = GovernedToolExecutor(
            registry,
            policy=PolicyEngine(),
            approval=StaticApprovalHandler(approved=False),
            session_mode=SessionMode.NON_INTERACTIVE,
            trust_source=TrustSource.SUBAGENT,
        )
        assert tuple(item.name for item in executor.definitions) == profile.tool_names
        self.executors.append(executor)
        return executor


def parent_executor(
    supervisor: SubagentSupervisor,
    *,
    policy: PolicyEngine | None = None,
) -> GovernedToolExecutor:
    return GovernedToolExecutor(
        ToolRegistry(build_subagent_tools((supervisor,))),
        policy=policy or PolicyEngine(),
        approval=StaticApprovalHandler(approved=False),
        session_mode=SessionMode.NON_INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )


def supervisor_for(
    tmp_path: Path,
    *,
    profile: SubagentProfile,
    providers: tuple[ModelProvider, ...],
    events: RecordingSubagentEventSink | None = None,
) -> tuple[
    SubagentSupervisor,
    RecordingProviderFactory,
    RealReadOnlyToolFactory,
]:
    provider_factory = RecordingProviderFactory(providers)
    tool_factory = RealReadOnlyToolFactory()
    child_ids = iter(f"child-{index + 1}" for index in range(len(providers)))
    supervisor = SubagentSupervisor(
        profile,
        workspace_root=tmp_path,
        provider_factory=provider_factory,
        tool_factory=tool_factory,
        events=events,
        id_factory=lambda: next(child_ids),
    )
    return supervisor, provider_factory, tool_factory


def workspace_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def prepare_workspace(tmp_path: Path) -> dict[str, bytes]:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_bytes(b"def run():\n    return 'needle'\n")
    return workspace_snapshot(tmp_path)


def delegated_payload(parent: ScriptedProvider) -> dict[str, object]:
    result_message = parent.requests[1].messages[-1]
    result = result_message.tool_results[0]
    return cast(dict[str, object], json.loads(result.content))


@pytest.mark.asyncio
async def test_real_parent_and_children_run_governed_read_only_path(
    tmp_path: Path,
) -> None:
    before = prepare_workspace(tmp_path)
    tasks = ("Inspect parser behavior.", "Find needle references.")
    children = (child_read_provider(), child_search_provider())
    events = RecordingSubagentEventSink()
    supervisor, provider_factory, tool_factory = supervisor_for(
        tmp_path,
        profile=profile_for(),
        providers=children,
        events=events,
    )
    parent = parent_provider_for(tasks)

    result = await AgentRuntime(
        parent,
        parent_executor(supervisor),
    ).run(
        user_prompt="Delegate two independent reviews.",
        run_id="parent-subagent-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    assert result.tool_calls == 1
    assert provider_factory.calls == [
        ("review", "child-1"),
        ("review", "child-2"),
    ]
    assert children[0].requests[0].messages == (Message.user_text(tasks[0]),)
    assert children[1].requests[0].messages == (Message.user_text(tasks[1]),)
    child_run_ids = {
        request.request_id.rsplit(":", 1)[0] for child in children for request in child.requests[:1]
    }
    assert len(child_run_ids) == 2
    assert all(run_id.startswith("subagent-") for run_id in child_run_ids)

    payload = delegated_payload(parent)
    assert payload["content_type"] == "subagent_batch_result"
    projected_children = cast(list[dict[str, object]], payload["children"])
    assert [child["ordinal"] for child in projected_children] == [0, 1]
    assert [child["untrusted_summary"] for child in projected_children] == [
        "Read review complete.",
        "Search review complete.",
    ]
    evidence = [cast(list[dict[str, object]], child["evidence"])[0] for child in projected_children]
    assert [item["tool_name"] for item in evidence] == [
        "read_file",
        "search_text",
    ]
    assert all(len(cast(str, item["content_sha256"])) == 64 for item in evidence)
    assert all(
        executor.trust_source_for(name) is TrustSource.SUBAGENT
        for executor in tool_factory.executors
        for name in ("read_file", "search_text")
    )

    event_payload = json.dumps(
        [event.model_dump(mode="json") for event in events.events],
        ensure_ascii=True,
        sort_keys=True,
    )
    for secret in (
        *tasks,
        supervisor.profile.system_prompt,
        "return 'needle'",
    ):
        assert secret not in event_payload
    assert workspace_snapshot(tmp_path) == before


@pytest.mark.asyncio
async def test_parent_policy_deny_prevents_child_composition(
    tmp_path: Path,
) -> None:
    prepare_workspace(tmp_path)
    supervisor, provider_factory, tool_factory = supervisor_for(
        tmp_path,
        profile=profile_for(),
        providers=(child_read_provider(),),
    )
    parent = parent_provider_for(("Inspect parser behavior.",))
    policy = PolicyEngine(
        (
            PolicyRule(
                id="deny-delegation",
                decision=PolicyDecision.DENY,
                rationale="Delegation disabled.",
                tool_glob="delegate_analysis",
            ),
        )
    )

    result = await AgentRuntime(
        parent,
        parent_executor(supervisor, policy=policy),
    ).run(
        user_prompt="Delegate review.",
        run_id="denied-subagent-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    denied = parent.requests[1].messages[-1].tool_results[0]
    assert json.loads(denied.content)["error"]["code"] == "permission_denied"
    assert provider_factory.calls == []
    assert tool_factory.executors == []


@pytest.mark.asyncio
async def test_child_cannot_recursively_call_parent_delegation_tool(
    tmp_path: Path,
) -> None:
    prepare_workspace(tmp_path)
    recursive = ScriptedProvider(
        (
            tool_response(
                ToolCall(
                    id="recursive-1",
                    name="delegate_analysis",
                    arguments={
                        "tasks": ["nested"],
                        "reason": "Try recursion.",
                    },
                )
            ),
            stop_response("Recursion was unavailable."),
        )
    )
    supervisor, _, _ = supervisor_for(
        tmp_path,
        profile=profile_for(),
        providers=(recursive,),
    )
    parent = parent_provider_for(("Inspect recursion boundary.",))

    result = await AgentRuntime(
        parent,
        parent_executor(supervisor),
    ).run(
        user_prompt="Delegate review.",
        run_id="nonrecursive-subagent-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    recursive_result = recursive.requests[1].messages[-1].tool_results[0]
    assert json.loads(recursive_result.content)["error"]["code"] == "unknown_tool"
    projected = cast(
        list[dict[str, object]],
        delegated_payload(parent)["children"],
    )
    assert projected[0]["untrusted_summary"] == "Recursion was unavailable."


@pytest.mark.asyncio
async def test_one_child_timeout_does_not_stop_sibling_or_parent(
    tmp_path: Path,
) -> None:
    prepare_workspace(tmp_path)
    slow = ScriptedProvider(
        (stop_response("too late"),),
        delay_seconds=0.2,
    )
    fast = ScriptedProvider((stop_response("sibling complete"),))
    supervisor, _, _ = supervisor_for(
        tmp_path,
        profile=profile_for(
            child_timeout_seconds=0.03,
            batch_timeout_seconds=0.3,
        ),
        providers=(slow, fast),
    )
    parent = parent_provider_for(("Slow review.", "Fast review."))

    result = await AgentRuntime(
        parent,
        parent_executor(supervisor),
    ).run(
        user_prompt="Delegate reviews.",
        run_id="timeout-subagent-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    projected = cast(
        list[dict[str, object]],
        delegated_payload(parent)["children"],
    )
    assert [child["status"] for child in projected] == [
        "timed_out",
        "completed",
    ]
    assert projected[1]["untrusted_summary"] == "sibling complete"


class CancellationGate:
    def __init__(self, expected: int) -> None:
        self.expected = expected
        self.active = 0
        self.cancelled = 0
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self) -> None:
        self.active += 1
        if self.active >= self.expected:
            self.reached.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


class BlockingProvider:
    def __init__(self, gate: CancellationGate) -> None:
        self._gate = gate
        self._capabilities = ProviderCapabilities()
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        await self._gate.wait()
        return stop_response("released")

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ProviderStreamEvent]:
        yield ResponseCompleted(response=await self.complete(request))


@pytest.mark.asyncio
async def test_parent_cancellation_cancels_both_children(
    tmp_path: Path,
) -> None:
    prepare_workspace(tmp_path)
    gate = CancellationGate(expected=2)
    children: tuple[ModelProvider, ...] = (
        BlockingProvider(gate),
        BlockingProvider(gate),
    )
    supervisor, _, _ = supervisor_for(
        tmp_path,
        profile=profile_for(),
        providers=children,
    )
    parent = parent_provider_for(("First review.", "Second review."))
    run = asyncio.create_task(
        AgentRuntime(
            parent,
            parent_executor(supervisor),
        ).run(
            user_prompt="Delegate reviews.",
            run_id="cancelled-subagent-run",
        )
    )
    await asyncio.wait_for(gate.reached.wait(), timeout=1)

    run.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run
    await asyncio.sleep(0)
    assert gate.cancelled == 2
    assert gate.active == 0
