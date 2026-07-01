from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import cast

import pytest
from pydantic import JsonValue

import mini_code_agent.subagents as subagent_api
from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import RiskLevel, SessionMode, TrustSource
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.models import (
    SubagentBatchResult,
    SubagentChildResult,
    SubagentError,
    SubagentErrorCode,
    SubagentLimits,
    SubagentProfile,
    SubagentStatus,
)
from mini_code_agent.subagents.tools import (
    SubagentAnalysisTool,
    build_subagent_tools,
)
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry


def profile_for(
    *,
    profile_id: str = "review",
    local_name: str = "delegate_analysis",
    tool_names: tuple[str, ...] = ("read_file", "search_text"),
    max_tasks: int = 4,
    max_task_chars: int = 100,
    max_result_bytes: int = 131_072,
) -> SubagentProfile:
    return SubagentProfile(
        profile_id=profile_id,
        local_name=local_name,
        description="Run an isolated code review.",
        system_prompt="Review only the assigned task.",
        tool_names=tool_names,
        agent_limits=AgentLimits(max_turns=4, max_tool_calls=8),
        limits=SubagentLimits(
            max_tasks=max_tasks,
            max_concurrency=min(2, max_tasks),
            max_task_chars=max_task_chars,
            max_evidence_items=8,
            max_result_bytes=max_result_bytes,
        ),
    )


def child_for(
    ordinal: int,
    *,
    summary: str = "done",
) -> SubagentChildResult:
    return SubagentChildResult(
        child_id=f"child-{ordinal + 1}",
        ordinal=ordinal,
        profile_id="review",
        status=SubagentStatus.COMPLETED,
        stop_reason=StopReason.COMPLETED,
        turns=1,
        tool_calls=0,
        untrusted_summary=summary,
        result_sha256="a" * 64,
    )


def batch_for(
    count: int = 2,
    *,
    summary: str = "done",
) -> SubagentBatchResult:
    return SubagentBatchResult.from_children(
        profile_id="review",
        children=tuple(child_for(index, summary=summary) for index in range(count)),
        duration_ms=10,
    )


class FakeSupervisor:
    def __init__(
        self,
        profile: SubagentProfile,
        *,
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.profile = profile
        self.result = result if result is not None else batch_for()
        self.error = error
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def run_batch(
        self,
        *,
        parent_tool_call_id: str,
        tasks: tuple[str, ...],
    ) -> SubagentBatchResult:
        self.calls.append((parent_tool_call_id, tasks))
        if self.error is not None:
            raise self.error
        return cast(SubagentBatchResult, self.result)


def call_for(
    *,
    tasks: tuple[str, ...] = ("one", "two"),
    reason: str = "Independent review.",
    name: str = "delegate_analysis",
    extra: Mapping[str, JsonValue] | None = None,
) -> ToolCall:
    arguments: dict[str, JsonValue] = {
        "tasks": list(tasks),
        "reason": reason,
    }
    arguments.update(extra or {})
    return ToolCall(
        id="parent-1",
        name=name,
        arguments=arguments,
    )


def error_code(content: str) -> str:
    return cast(str, json.loads(content)["error"]["code"])


def test_definition_snapshots_profile_specific_schema() -> None:
    profile = profile_for(max_tasks=3, max_task_chars=77)
    tool = SubagentAnalysisTool(FakeSupervisor(profile))
    schema = tool.definition.model_dump(mode="json")["input_schema"]

    assert tool.definition.name == "delegate_analysis"
    assert tool.definition.description == profile.description
    assert tool.definition.side_effect is SideEffect.READ_ONLY
    assert schema["properties"]["tasks"]["maxItems"] == 3
    assert schema["properties"]["tasks"]["items"]["maxLength"] == 77
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_preview_is_read_only_medium_risk_and_bounded() -> None:
    tool = SubagentAnalysisTool(FakeSupervisor(profile_for()))

    preview = await tool.preview(
        call_for(
            tasks=("Inspect parser.", "Inspect serializer."),
            reason="Independent review.",
        )
    )

    assert preview.side_effect is SideEffect.READ_ONLY
    assert preview.risk is RiskLevel.MEDIUM
    assert preview.resources == (".",)
    assert "2" in preview.summary
    assert preview.reason == "Independent review."
    assert "Inspect parser." not in preview.model_dump_json()


@pytest.mark.asyncio
async def test_execute_returns_deterministic_bounded_batch_json() -> None:
    supervisor = FakeSupervisor(profile_for())
    tool = SubagentAnalysisTool(supervisor)

    first = await tool.execute(call_for())
    second = await tool.execute(call_for())
    payload = json.loads(first.content)

    assert first.is_error is False
    assert first.content == second.content
    assert payload["content_type"] == "subagent_batch_result"
    assert [child["ordinal"] for child in payload["children"]] == [0, 1]
    assert supervisor.calls == [
        ("parent-1", ("one", "two")),
        ("parent-1", ("one", "two")),
    ]
    first.content.encode("ascii")


@pytest.mark.asyncio
async def test_tool_runs_through_registry_and_governed_policy_path() -> None:
    supervisor = FakeSupervisor(profile_for())
    tool = SubagentAnalysisTool(supervisor)
    executor = GovernedToolExecutor(
        ToolRegistry((tool,)),
        policy=PolicyEngine(),
        approval=StaticApprovalHandler(approved=False),
        session_mode=SessionMode.NON_INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )

    result = await executor.execute(call_for())
    duplicate = await executor.execute(call_for(tasks=("same", "same")))

    assert result.is_error is False
    assert error_code(duplicate.content) == "invalid_arguments"
    assert supervisor.calls == [("parent-1", ("one", "two"))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        call_for(extra={"unexpected": True}),
        call_for(tasks=()),
        call_for(tasks=("one", "two", "three", "four", "five")),
        call_for(tasks=("same", "same")),
        call_for(tasks=("x" * 101,)),
        call_for(reason=""),
        call_for(reason="x" * 501),
        call_for(reason="bad\0reason"),
    ],
)
async def test_execute_rejects_invalid_arguments(call: ToolCall) -> None:
    supervisor = FakeSupervisor(profile_for())
    result = await SubagentAnalysisTool(supervisor).execute(call)

    assert result.is_error is True
    assert error_code(result.content) == "invalid_arguments"
    assert supervisor.calls == []


@pytest.mark.asyncio
async def test_execute_rejects_wrong_tool_name() -> None:
    result = await SubagentAnalysisTool(FakeSupervisor(profile_for())).execute(
        call_for(name="other_tool")
    )

    assert result.is_error is True
    assert error_code(result.content) == "unknown_tool"


@pytest.mark.asyncio
async def test_execute_rejects_oversized_serialized_result() -> None:
    profile = profile_for(max_result_bytes=100)
    supervisor = FakeSupervisor(
        profile,
        result=batch_for(summary="x" * 500),
    )

    result = await SubagentAnalysisTool(supervisor).execute(call_for())

    assert result.is_error is True
    assert error_code(result.content) == SubagentErrorCode.RESULT_TOO_LARGE.value
    assert "x" * 100 not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            SubagentError(
                SubagentErrorCode.INVALID_BATCH,
                "Subagent batch request was invalid.",
            ),
            SubagentErrorCode.INVALID_BATCH.value,
        ),
        (
            SubagentCompositionError(),
            SubagentErrorCode.COMPOSITION_FAILED.value,
        ),
        (
            RuntimeError("secret failure"),
            SubagentErrorCode.CHILD_FAILED.value,
        ),
    ],
)
async def test_execute_maps_supervisor_failures_to_static_errors(
    failure: Exception,
    expected_code: str,
) -> None:
    supervisor = FakeSupervisor(profile_for(), error=failure)

    result = await SubagentAnalysisTool(supervisor).execute(call_for())

    assert result.is_error is True
    assert error_code(result.content) == expected_code
    assert "secret" not in result.content


@pytest.mark.asyncio
async def test_execute_rejects_malformed_supervisor_result() -> None:
    supervisor = FakeSupervisor(profile_for(), result={"not": "a batch"})

    result = await SubagentAnalysisTool(supervisor).execute(call_for())

    assert result.is_error is True
    assert error_code(result.content) == SubagentErrorCode.CHILD_FAILED.value


@pytest.mark.asyncio
async def test_execute_re_raises_cancellation() -> None:
    supervisor = FakeSupervisor(profile_for(), error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await SubagentAnalysisTool(supervisor).execute(call_for())


@pytest.mark.parametrize(
    "profiles",
    [
        (
            profile_for(profile_id="same", local_name="analysis_a"),
            profile_for(profile_id="same", local_name="analysis_b"),
        ),
        (
            profile_for(profile_id="one", local_name="same_name"),
            profile_for(profile_id="two", local_name="same_name"),
        ),
        (
            profile_for(profile_id="one", local_name="analysis_a"),
            profile_for(
                profile_id="two",
                local_name="analysis_b",
                tool_names=("analysis_a",),
            ),
        ),
    ],
)
def test_builder_rejects_profile_and_parent_child_name_conflicts(
    profiles: tuple[SubagentProfile, SubagentProfile],
) -> None:
    supervisors = tuple(FakeSupervisor(profile) for profile in profiles)

    with pytest.raises(ValueError, match="Subagent Tool profiles conflict"):
        build_subagent_tools(supervisors)


def test_builder_returns_one_distinct_tool_per_profile() -> None:
    profiles = (
        profile_for(profile_id="one", local_name="analysis_a"),
        profile_for(profile_id="two", local_name="analysis_b"),
    )

    tools = build_subagent_tools(tuple(FakeSupervisor(profile) for profile in profiles))

    assert [tool.definition.name for tool in tools] == ["analysis_a", "analysis_b"]
    assert tools[0].definition is not tools[1].definition


def test_package_exports_stable_subagent_api() -> None:
    expected = {
        "NullSubagentEventSink",
        "RecordingSubagentEventSink",
        "SubagentAnalysisTool",
        "SubagentBatchCompleted",
        "SubagentBatchResult",
        "SubagentBatchStarted",
        "SubagentChildResult",
        "SubagentCompleted",
        "SubagentCompositionError",
        "SubagentError",
        "SubagentErrorCode",
        "SubagentEvent",
        "SubagentEventSink",
        "SubagentEvidenceItem",
        "SubagentLimits",
        "SubagentProfile",
        "SubagentProviderFactory",
        "SubagentStarted",
        "SubagentStatus",
        "SubagentSupervisor",
        "SubagentToolFactory",
        "build_subagent_tools",
    }

    assert expected.issubset(set(subagent_api.__all__))
