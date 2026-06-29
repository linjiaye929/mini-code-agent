import asyncio

import pytest

from mini_code_agent.agent.events import RecordingEventSink, RunStopped
from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
)
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.runtime_info import RuntimeInfoTool


def final_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
    )


def tool_response(call_id: str) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=(ToolCall(id=call_id, name="runtime_info", arguments={}),),
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


class SlowTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        await asyncio.sleep(10)
        return await super().execute(call)


class RaisingTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        del call
        raise RuntimeError("internal-tool-secret")


class MismatchedTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        del call
        return ToolResult(tool_call_id="wrong-id", content="incorrect")


class ExplodingProvider(ScriptedProvider):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise RuntimeError("internal-provider-secret")


@pytest.mark.asyncio
async def test_runtime_completes_with_final_text() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([final_response("done")]),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_text == "done"
    assert result.turns == 1
    assert result.tool_calls == 0


@pytest.mark.asyncio
async def test_runtime_stops_at_max_turns() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1"), tool_response("call-2")]),
        RuntimeInfoTool(),
        limits=AgentLimits(max_turns=2),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.MAX_TURNS
    assert result.turns == 2
    assert result.tool_calls == 2


@pytest.mark.asyncio
async def test_runtime_rejects_duplicate_tool_call_ids() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1"), tool_response("call-1")]),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.DUPLICATE_TOOL_CALL
    assert result.tool_calls == 1


@pytest.mark.asyncio
async def test_runtime_stops_on_normalized_provider_error() -> None:
    runtime = AgentRuntime(
        ScriptedProvider(
            [
                ProviderError(
                    ProviderErrorCode.AUTHENTICATION,
                    "Provider authentication failed.",
                    retryable=False,
                )
            ]
        ),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_ERROR
    assert result.error == "Provider authentication failed."


@pytest.mark.asyncio
async def test_runtime_hides_unexpected_provider_exception() -> None:
    runtime = AgentRuntime(ExplodingProvider([]), RuntimeInfoTool())

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_ERROR
    assert result.error == "Provider request failed unexpectedly."
    assert "internal-provider-secret" not in result.error


@pytest.mark.asyncio
async def test_runtime_stops_on_provider_timeout() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([final_response("late")], delay_seconds=0.05),
        RuntimeInfoTool(),
        limits=AgentLimits(provider_timeout_seconds=0.01),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_TIMEOUT


@pytest.mark.asyncio
async def test_runtime_re_raises_task_cancellation_after_event() -> None:
    sink = RecordingEventSink()
    runtime = AgentRuntime(
        ScriptedProvider([final_response("late")], delay_seconds=10),
        RuntimeInfoTool(),
        events=sink,
    )
    task = asyncio.create_task(runtime.run(user_prompt="inspect"))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    stopped = [event for event in sink.events if isinstance(event, RunStopped)]
    assert stopped[-1].reason is StopReason.CANCELLED


@pytest.mark.asyncio
async def test_runtime_stops_before_exceeding_tool_call_limit() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1")]),
        RuntimeInfoTool(),
        limits=AgentLimits(max_tool_calls=0),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert result.tool_calls == 0


@pytest.mark.asyncio
async def test_tool_timeout_becomes_correlated_error_result() -> None:
    provider = ScriptedProvider([tool_response("call-1"), final_response("recovered")])
    runtime = AgentRuntime(
        provider,
        SlowTool(),
        limits=AgentLimits(tool_timeout_seconds=0.01),
    )

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True
    assert "tool_timeout" in tool_result.content


@pytest.mark.asyncio
async def test_unexpected_tool_exception_is_not_exposed() -> None:
    provider = ScriptedProvider([tool_response("call-1"), final_response("recovered")])
    runtime = AgentRuntime(provider, RaisingTool())

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.is_error is True
    assert "tool_failed" in tool_result.content
    assert "internal-tool-secret" not in tool_result.content


@pytest.mark.asyncio
async def test_mismatched_tool_result_id_is_recorrelated() -> None:
    provider = ScriptedProvider([tool_response("call-1"), final_response("recovered")])
    runtime = AgentRuntime(provider, MismatchedTool())

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True
    assert "invalid_tool_result" in tool_result.content


@pytest.mark.asyncio
async def test_max_tokens_maps_to_provider_limit() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message.assistant_text("partial"),
                finish_reason=FinishReason.MAX_TOKENS,
            )
        ]
    )
    runtime = AgentRuntime(provider, RuntimeInfoTool())

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_LIMIT
    assert result.succeeded is False


@pytest.mark.asyncio
async def test_every_executed_tool_call_has_exactly_one_result() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="call-1",
                            name="runtime_info",
                            arguments={},
                        ),
                        ToolCall(
                            id="call-2",
                            name="runtime_info",
                            arguments={},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            final_response("done"),
        ]
    )
    runtime = AgentRuntime(provider, RuntimeInfoTool())

    result = await runtime.run(user_prompt="inspect")

    results = provider.requests[1].messages[-1].tool_results
    assert result.stop_reason is StopReason.COMPLETED
    assert [item.tool_call_id for item in results] == ["call-1", "call-2"]
    assert len(results) == 2
