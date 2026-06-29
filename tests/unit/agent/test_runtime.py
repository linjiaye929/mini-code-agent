import asyncio
from typing import cast

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
from mini_code_agent.tools.base import SideEffect, ToolDefinition
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


def named_tool_response(call_id: str, name: str) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=(ToolCall(id=call_id, name=name, arguments={}),),
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


class RecordingTool(RuntimeInfoTool):
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return await super().execute(call)


class ExplodingProvider(ScriptedProvider):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise RuntimeError("internal-provider-secret")


class InvalidProvider(ScriptedProvider):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        return cast(ModelResponse, None)


class InvalidTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        del call
        return cast(ToolResult, None)


class WriteTool(RuntimeInfoTool):
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name="write_tool",
                description="A write-capable test tool.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                side_effect=SideEffect.WRITE,
            ),
        )


class FailingEventSink:
    def __init__(self, fail_type: type[object]) -> None:
        self._fail_type = fail_type

    def publish(self, event: object) -> None:
        if isinstance(event, self._fail_type):
            raise RuntimeError("sink-failed")


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
async def test_invalid_provider_return_maps_to_invalid_response() -> None:
    runtime = AgentRuntime(InvalidProvider([]), RuntimeInfoTool())

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.INVALID_RESPONSE
    assert result.error == "Provider returned an invalid response."


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
async def test_runtime_records_cancellation_during_tool_execution() -> None:
    sink = RecordingEventSink()
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1")]),
        SlowTool(),
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
async def test_invalid_tool_return_becomes_correlated_error() -> None:
    provider = ScriptedProvider([tool_response("call-1"), final_response("recovered")])
    runtime = AgentRuntime(provider, InvalidTool())

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


@pytest.mark.asyncio
async def test_duplicate_id_in_one_batch_executes_nothing() -> None:
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
                            id="call-1",
                            name="runtime_info",
                            arguments={},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            )
        ]
    )
    tools = RecordingTool()
    runtime = AgentRuntime(provider, tools)

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.DUPLICATE_TOOL_CALL
    assert result.tool_calls == 0
    assert tools.calls == []


@pytest.mark.asyncio
async def test_over_budget_batch_executes_nothing() -> None:
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
            )
        ]
    )
    tools = RecordingTool()
    runtime = AgentRuntime(
        provider,
        tools,
        limits=AgentLimits(max_tool_calls=1),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert result.tool_calls == 0
    assert tools.calls == []


def test_runtime_rejects_non_read_only_tools() -> None:
    with pytest.raises(ValueError, match="M1 only permits read-only tools"):
        AgentRuntime(ScriptedProvider([final_response("done")]), WriteTool())


@pytest.mark.asyncio
async def test_unregistered_tool_never_reaches_executor() -> None:
    provider = ScriptedProvider(
        [named_tool_response("call-1", "unknown_tool"), final_response("recovered")]
    )
    tools = RecordingTool()
    runtime = AgentRuntime(provider, tools)

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True
    assert "unknown_tool" in tool_result.content
    assert tools.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fail_type",
    [
        "run_started",
        "model_completed",
        "tool_completed",
        "run_stopped",
    ],
)
async def test_event_sink_failure_never_aborts_run(fail_type: str) -> None:
    from mini_code_agent.agent.events import (
        ModelCompleted,
        RunStarted,
        ToolCompleted,
    )

    event_types = {
        "run_started": RunStarted,
        "model_completed": ModelCompleted,
        "tool_completed": ToolCompleted,
        "run_stopped": RunStopped,
    }
    provider = ScriptedProvider([tool_response("call-1"), final_response("done")])
    runtime = AgentRuntime(
        provider,
        RuntimeInfoTool(),
        events=FailingEventSink(event_types[fail_type]),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.COMPLETED


@pytest.mark.asyncio
async def test_event_sink_failure_does_not_mask_cancellation() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([final_response("late")], delay_seconds=10),
        RuntimeInfoTool(),
        events=FailingEventSink(RunStopped),
    )
    task = asyncio.create_task(runtime.run(user_prompt="inspect"))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_id_is_validated_before_start_event() -> None:
    sink = RecordingEventSink()
    runtime = AgentRuntime(
        ScriptedProvider([final_response("done")]),
        RuntimeInfoTool(),
        events=sink,
    )

    with pytest.raises(ValueError, match="run_id"):
        await runtime.run(user_prompt="inspect", run_id="x" * 129)

    assert sink.events == []
