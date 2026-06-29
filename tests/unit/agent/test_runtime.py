import asyncio
from typing import cast

import pytest

from mini_code_agent.agent.events import (
    AgentEvent,
    ContextCompacted,
    ModelCompleted,
    ModelStarted,
    RecordingEventSink,
    RunStarted,
    RunStopped,
    ToolCompleted,
    ToolStarted,
)
from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.context.errors import ContextError, ContextErrorCode
from mini_code_agent.context.manager import ContextPreparer
from mini_code_agent.context.models import ContextWindow
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


class RecordingContext:
    def __init__(self, *, compact_on_message_count: int | None = None) -> None:
        self.calls: list[tuple[str, tuple[Message, ...], tuple[ToolDefinition, ...]]] = []
        self._compact_on_message_count = compact_on_message_count

    def prepare(
        self,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> ContextWindow:
        self.calls.append((system_prompt, messages, tools))
        compact = (
            self._compact_on_message_count is not None
            and len(messages) >= self._compact_on_message_count
        )
        selected = (messages[0], *messages[-2:]) if compact else messages
        return ContextWindow(
            system_prompt=(
                f"{system_prompt}\n\n[context-omitted test]" if compact else system_prompt
            ),
            messages=selected,
            estimated_before=len(messages) * 100,
            estimated_after=len(selected) * 100,
            omitted_messages=len(messages) - len(selected),
            omitted_tool_exchanges=1 if compact else 0,
            transcript_sha256="a" * 64,
        )


class FailingContext:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def prepare(
        self,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> ContextWindow:
        del system_prompt, messages, tools
        raise self._error


class InvalidContext:
    def prepare(
        self,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> ContextWindow:
        del system_prompt, messages, tools
        return cast(ContextWindow, None)


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


class GovernedWriteTool(WriteTool):
    @property
    def governance_enforced(self) -> bool:
        return True


class TruthyMarkerWriteTool(WriteTool):
    @property
    def governance_enforced(self) -> str:
        return "true"


class FailingEventSink:
    def __init__(self, fail_type: type[object]) -> None:
        self._fail_type = fail_type

    def publish(self, event: object) -> None:
        if isinstance(event, self._fail_type):
            raise RuntimeError("sink-failed")


class RecordingJournal:
    def __init__(
        self,
        fail_type: type[object] | None = None,
        *,
        before_append: object | None = None,
    ) -> None:
        self.events: list[AgentEvent] = []
        self.attempts: list[AgentEvent] = []
        self._fail_type = fail_type
        self._before_append = before_append

    def append(self, event: AgentEvent) -> None:
        self.attempts.append(event)
        if callable(self._before_append):
            self._before_append(event)
        if self._fail_type is not None and isinstance(event, self._fail_type):
            raise RuntimeError("secret-journal-failure")
        self.events.append(event)


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
async def test_normalized_provider_timeout_uses_timeout_stop_reason() -> None:
    runtime = AgentRuntime(
        ScriptedProvider(
            [
                ProviderError(
                    ProviderErrorCode.TIMEOUT,
                    "Provider request timed out.",
                    retryable=True,
                )
            ]
        ),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_TIMEOUT
    assert result.error == "Provider request timed out."


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


def test_runtime_rejects_ungoverned_side_effecting_tools() -> None:
    with pytest.raises(ValueError, match="require governed execution"):
        AgentRuntime(ScriptedProvider([final_response("done")]), WriteTool())


def test_runtime_rejects_non_boolean_governance_marker() -> None:
    with pytest.raises(ValueError, match="require governed execution"):
        AgentRuntime(
            ScriptedProvider([final_response("done")]),
            TruthyMarkerWriteTool(),
        )


def test_runtime_accepts_governed_side_effecting_tools() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([final_response("done")]),
        GovernedWriteTool(),
    )

    assert runtime is not None


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
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "run_stopped",
    ],
)
async def test_event_sink_failure_never_aborts_run(fail_type: str) -> None:
    event_types = {
        "run_started": RunStarted,
        "model_started": ModelStarted,
        "model_completed": ModelCompleted,
        "tool_started": ToolStarted,
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


@pytest.mark.asyncio
async def test_context_is_prepared_before_every_provider_turn() -> None:
    context = RecordingContext()
    provider = ScriptedProvider([tool_response("call-1"), final_response("done")])
    runtime = AgentRuntime(provider, RuntimeInfoTool(), context=context)

    result = await runtime.run(
        user_prompt="inspect",
        system_prompt="system",
        run_id="context-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    assert len(context.calls) == 2
    assert context.calls[0][0] == "system"
    assert len(context.calls[0][1]) == 1
    assert len(context.calls[1][1]) == 3
    assert context.calls[0][2][0].name == "runtime_info"


@pytest.mark.asyncio
async def test_runtime_sends_compacted_window_but_returns_full_transcript() -> None:
    context = RecordingContext(compact_on_message_count=5)
    events = RecordingEventSink()
    provider = ScriptedProvider(
        [
            tool_response("call-1"),
            tool_response("call-2"),
            final_response("done"),
        ]
    )
    runtime = AgentRuntime(
        provider,
        RuntimeInfoTool(),
        context=context,
        events=events,
    )

    result = await runtime.run(user_prompt="inspect", run_id="compaction-run")

    assert result.stop_reason is StopReason.COMPLETED
    assert len(provider.requests[2].messages) == 3
    assert len(result.messages) == 6
    compacted = [event for event in events.events if isinstance(event, ContextCompacted)]
    assert len(compacted) == 1
    assert compacted[0].turn == 3
    assert compacted[0].omitted_messages == 2
    assert compacted[0].omitted_tool_exchanges == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        FailingContext(
            ContextError(
                ContextErrorCode.FIXED_CONTENT_TOO_LARGE,
                "secret-context-error",
            )
        ),
        FailingContext(RuntimeError("secret-unexpected-error")),
        InvalidContext(),
    ],
)
async def test_context_failure_stops_before_provider_with_static_error(
    context: object,
) -> None:
    provider = ScriptedProvider([final_response("must-not-run")])
    runtime = AgentRuntime(
        provider,
        RuntimeInfoTool(),
        context=cast(ContextPreparer, context),
    )

    result = await runtime.run(user_prompt="secret-user-goal", run_id="context-failure")

    assert result.stop_reason is StopReason.CONTEXT_LIMIT
    assert result.turns == 0
    assert result.error == "Model context limit exceeded."
    assert provider.requests == []
    assert len(result.messages) == 1
    assert "secret-context-error" not in (result.error or "")
    assert "secret-unexpected-error" not in (result.error or "")
    assert "secret-user-goal" not in (result.error or "")


@pytest.mark.asyncio
async def test_context_event_sink_failure_does_not_change_compaction() -> None:
    context = RecordingContext(compact_on_message_count=5)
    provider = ScriptedProvider(
        [
            tool_response("call-1"),
            tool_response("call-2"),
            final_response("done"),
        ]
    )
    runtime = AgentRuntime(
        provider,
        RuntimeInfoTool(),
        context=context,
        events=FailingEventSink(ContextCompacted),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.COMPLETED
    assert len(provider.requests[2].messages) == 3


@pytest.mark.asyncio
async def test_required_journal_records_ordered_runtime_lifecycle() -> None:
    tools = RecordingTool()

    def assert_before_execution(event: AgentEvent) -> None:
        if isinstance(event, ToolStarted):
            assert tools.calls == []

    journal = RecordingJournal(before_append=assert_before_execution)
    provider = ScriptedProvider([tool_response("call-1"), final_response("done")])
    runtime = AgentRuntime(
        provider,
        tools,
        journal=journal,
    )

    result = await runtime.run(user_prompt="inspect", run_id="journal-run")

    assert result.stop_reason is StopReason.COMPLETED
    assert tuple(event.type for event in journal.events) == (
        "run_started",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "model_started",
        "model_completed",
        "run_stopped",
    )
    assert journal.events[1].request_id == "journal-run:1"  # type: ignore[union-attr]
    started = journal.events[3]
    assert isinstance(started, ToolStarted)
    assert started.side_effect is SideEffect.READ_ONLY
    stopped = journal.events[-1]
    assert isinstance(stopped, RunStopped)
    assert stopped.turns == 2
    assert stopped.tool_calls == 1
    assert stopped.usage == result.usage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "fail_type",
        "expected_provider_calls",
        "expected_tool_calls",
        "expected_messages",
    ),
    [
        (RunStarted, 0, 0, 1),
        (ModelStarted, 0, 0, 1),
        (ModelCompleted, 1, 0, 2),
        (ToolStarted, 1, 0, 2),
        (ToolCompleted, 1, 1, 2),
        (RunStopped, 2, 1, 4),
    ],
)
async def test_required_journal_failure_stops_all_later_work(
    fail_type: type[object],
    expected_provider_calls: int,
    expected_tool_calls: int,
    expected_messages: int,
) -> None:
    journal = RecordingJournal(fail_type)
    provider = ScriptedProvider([tool_response("call-1"), final_response("done")])
    tools = RecordingTool()
    observer = RecordingEventSink()
    runtime = AgentRuntime(
        provider,
        tools,
        events=observer,
        journal=journal,
    )

    result = await runtime.run(
        user_prompt="secret-user-prompt",
        run_id="journal-failure",
    )

    assert result.stop_reason is StopReason.PERSISTENCE_ERROR
    assert result.error == "Agent state could not be persisted."
    assert "secret-journal-failure" not in (result.error or "")
    assert len(provider.requests) == expected_provider_calls
    assert len(tools.calls) == expected_tool_calls
    assert len(result.messages) == expected_messages
    stopped = [event for event in observer.events if isinstance(event, RunStopped)]
    assert stopped[-1].reason is StopReason.PERSISTENCE_ERROR


@pytest.mark.asyncio
async def test_required_journal_failure_during_cancel_does_not_mask_cancellation() -> None:
    journal = RecordingJournal(RunStopped)
    runtime = AgentRuntime(
        ScriptedProvider([final_response("late")], delay_seconds=10),
        RuntimeInfoTool(),
        journal=journal,
    )
    task = asyncio.create_task(runtime.run(user_prompt="inspect", run_id="cancel-run"))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    attempted_stop = journal.attempts[-1]
    assert isinstance(attempted_stop, RunStopped)
    assert attempted_stop.reason is StopReason.CANCELLED


@pytest.mark.asyncio
async def test_compaction_journal_failure_stops_before_next_provider() -> None:
    context = RecordingContext(compact_on_message_count=5)
    journal = RecordingJournal(ContextCompacted)
    provider = ScriptedProvider(
        [
            tool_response("call-1"),
            tool_response("call-2"),
            final_response("must-not-run"),
        ]
    )
    runtime = AgentRuntime(
        provider,
        RuntimeInfoTool(),
        context=context,
        journal=journal,
    )

    result = await runtime.run(user_prompt="inspect", run_id="compaction-journal")

    assert result.stop_reason is StopReason.PERSISTENCE_ERROR
    assert len(provider.requests) == 2
    assert len(result.messages) == 5
