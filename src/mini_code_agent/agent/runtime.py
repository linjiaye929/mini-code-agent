from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from mini_code_agent.agent.events import (
    AgentEvent,
    ContextCompacted,
    EventJournal,
    EventSink,
    ModelCompleted,
    ModelStarted,
    NullEventSink,
    RunStarted,
    RunStopped,
    ToolCompleted,
    ToolStarted,
)
from mini_code_agent.agent.models import AgentLimits, AgentResult, StopReason
from mini_code_agent.context.errors import ContextError
from mini_code_agent.context.manager import ContextManager, ContextPreparer
from mini_code_agent.context.models import ContextWindow
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
    TokenUsage,
)
from mini_code_agent.tools.base import SideEffect, ToolExecutor


class _JournalFailure(RuntimeError):
    pass


@dataclass(slots=True)
class _RunState:
    run_id: str
    messages: list[Message]
    usage: TokenUsage = field(default_factory=TokenUsage)
    seen_call_ids: set[str] = field(default_factory=lambda: set[str]())
    turns: int = 0
    tool_calls: int = 0


class AgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolExecutor,
        *,
        limits: AgentLimits | None = None,
        events: EventSink | None = None,
        journal: EventJournal | None = None,
        context: ContextPreparer | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._limits = limits or AgentLimits()
        self._events = events or NullEventSink()
        self._journal = journal
        self._context = context or ContextManager()
        definitions = tools.definitions
        names = tuple(definition.name for definition in definitions)
        if len(set(names)) != len(names):
            raise ValueError("Tool definitions must have unique names.")
        has_side_effects = any(
            definition.side_effect is not SideEffect.READ_ONLY for definition in definitions
        )
        if has_side_effects and getattr(tools, "governance_enforced", None) is not True:
            raise ValueError("Side-effecting tools require governed execution.")
        self._definitions = definitions
        self._tool_names = frozenset(names)
        self._side_effects = {definition.name: definition.side_effect for definition in definitions}

    async def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        run_id: str | None = None,
    ) -> AgentResult:
        active_run_id = self._validate_run_id(run_id or str(uuid4()))
        state = _RunState(
            run_id=active_run_id,
            messages=[Message.user_text(user_prompt)],
        )
        try:
            self._emit(
                RunStarted(
                    run_id=active_run_id,
                    max_turns=self._limits.max_turns,
                )
            )
            return await self._run_loop(state, system_prompt=system_prompt)
        except _JournalFailure:
            return self._persistence_failure(state)

    async def _run_loop(
        self,
        state: _RunState,
        *,
        system_prompt: str,
    ) -> AgentResult:
        for turn in range(1, self._limits.max_turns + 1):
            try:
                window_candidate = cast(
                    object,
                    self._context.prepare(
                        system_prompt=system_prompt,
                        messages=tuple(state.messages),
                        tools=self._definitions,
                    ),
                )
            except ContextError:
                return self._stop(
                    state,
                    StopReason.CONTEXT_LIMIT,
                    "Model context limit exceeded.",
                )
            except Exception:
                return self._stop(
                    state,
                    StopReason.CONTEXT_LIMIT,
                    "Model context limit exceeded.",
                )
            if not isinstance(window_candidate, ContextWindow):
                return self._stop(
                    state,
                    StopReason.CONTEXT_LIMIT,
                    "Model context limit exceeded.",
                )
            window = window_candidate
            if window.compacted:
                self._emit(
                    ContextCompacted(
                        run_id=state.run_id,
                        turn=turn,
                        estimated_before=window.estimated_before,
                        estimated_after=window.estimated_after,
                        omitted_messages=window.omitted_messages,
                        omitted_tool_exchanges=window.omitted_tool_exchanges,
                        transcript_sha256=window.transcript_sha256,
                    )
                )
            request = ModelRequest(
                request_id=f"{state.run_id}:{turn}",
                system_prompt=window.system_prompt,
                messages=window.messages,
                tools=self._definitions,
            )
            self._emit(
                ModelStarted(
                    run_id=state.run_id,
                    turn=turn,
                    request_id=request.request_id,
                )
            )
            try:
                async with asyncio.timeout(self._limits.provider_timeout_seconds):
                    response_candidate = cast(
                        object,
                        await self._provider.complete(request),
                    )
            except asyncio.CancelledError:
                self._emit_cancelled(state)
                raise
            except TimeoutError:
                return self._stop(
                    state,
                    StopReason.PROVIDER_TIMEOUT,
                    "Provider request timed out.",
                )
            except ProviderError as exc:
                reason = (
                    StopReason.PROVIDER_TIMEOUT
                    if exc.code is ProviderErrorCode.TIMEOUT
                    else StopReason.PROVIDER_ERROR
                )
                return self._stop(
                    state,
                    reason,
                    exc.public_message,
                )
            except Exception:
                return self._stop(
                    state,
                    StopReason.PROVIDER_ERROR,
                    "Provider request failed unexpectedly.",
                )

            if not isinstance(response_candidate, ModelResponse):
                return self._stop(
                    state,
                    StopReason.INVALID_RESPONSE,
                    "Provider returned an invalid response.",
                )
            response = response_candidate

            state.messages.append(response.message)
            state.turns = turn
            state.usage = TokenUsage(
                input_tokens=state.usage.input_tokens + response.usage.input_tokens,
                output_tokens=state.usage.output_tokens + response.usage.output_tokens,
            )
            self._emit(
                ModelCompleted(
                    run_id=state.run_id,
                    turn=turn,
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                )
            )

            if response.finish_reason is FinishReason.STOP:
                return self._stop(
                    state,
                    StopReason.COMPLETED,
                    final_text=response.message.text,
                )

            if response.finish_reason is not FinishReason.TOOL_CALL:
                return self._stop(
                    state,
                    StopReason.PROVIDER_LIMIT,
                    "Provider stopped before completing the response.",
                )

            calls = response.message.tool_calls
            call_ids = tuple(call.id for call in calls)
            if len(set(call_ids)) != len(call_ids) or state.seen_call_ids.intersection(call_ids):
                return self._stop(
                    state,
                    StopReason.DUPLICATE_TOOL_CALL,
                    "Provider repeated a ToolCall identifier.",
                )
            if state.tool_calls + len(calls) > self._limits.max_tool_calls:
                return self._stop(
                    state,
                    StopReason.MAX_TOOL_CALLS,
                    "Agent reached the ToolCall limit.",
                )

            state.seen_call_ids.update(call_ids)
            tool_results: list[ToolResult] = []
            for call in calls:
                self._emit(
                    ToolStarted(
                        run_id=state.run_id,
                        turn=turn,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        side_effect=self._side_effects.get(
                            call.name,
                            SideEffect.READ_ONLY,
                        ),
                    )
                )
                state.tool_calls += 1
                try:
                    result = await self._execute_tool(call)
                except asyncio.CancelledError:
                    self._emit_cancelled(state)
                    raise
                tool_results.append(result)
                self._emit(
                    ToolCompleted(
                        run_id=state.run_id,
                        turn=turn,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        is_error=result.is_error,
                    )
                )
            state.messages.append(
                Message(
                    role=MessageRole.USER,
                    content=tuple(tool_results),
                )
            )

        return self._stop(
            state,
            StopReason.MAX_TURNS,
            "Agent reached the turn limit.",
        )

    async def _execute_tool(self, call: ToolCall) -> ToolResult:
        if call.name not in self._tool_names:
            return self._tool_error(
                call.id,
                "unknown_tool",
                "The requested tool is not registered.",
            )
        try:
            async with asyncio.timeout(self._limits.tool_timeout_seconds):
                result_candidate = cast(
                    object,
                    await self._tools.execute(call),
                )
        except TimeoutError:
            return self._tool_error(
                call.id,
                "tool_timeout",
                "Tool execution timed out.",
            )
        except Exception:
            return self._tool_error(
                call.id,
                "tool_failed",
                "Tool execution failed.",
            )
        if not isinstance(result_candidate, ToolResult):
            return self._tool_error(
                call.id,
                "invalid_tool_result",
                "Tool returned an invalid result.",
            )
        result = result_candidate
        if result.tool_call_id != call.id:
            return self._tool_error(
                call.id,
                "invalid_tool_result",
                "Tool result ID mismatch.",
            )
        return result

    @staticmethod
    def _tool_error(call_id: str, code: str, message: str) -> ToolResult:
        content = json.dumps(
            {"error": {"code": code, "message": message}},
            ensure_ascii=True,
            sort_keys=True,
        )
        return ToolResult(tool_call_id=call_id, content=content, is_error=True)

    def _stop(
        self,
        state: _RunState,
        reason: StopReason,
        error: str | None = None,
        *,
        final_text: str | None = None,
    ) -> AgentResult:
        self._emit(
            RunStopped(
                run_id=state.run_id,
                turns=state.turns,
                reason=reason,
                tool_calls=state.tool_calls,
                usage=state.usage,
                error=_bounded_event_error(error),
            )
        )
        return AgentResult(
            run_id=state.run_id,
            messages=tuple(state.messages),
            stop_reason=reason,
            turns=state.turns,
            tool_calls=state.tool_calls,
            usage=state.usage,
            final_text=final_text,
            error=error,
        )

    def _emit(self, event: AgentEvent) -> None:
        if self._journal is not None:
            try:
                self._journal.append(event)
            except Exception:
                raise _JournalFailure from None
        self._publish(event)

    def _emit_cancelled(self, state: _RunState) -> None:
        event = RunStopped(
            run_id=state.run_id,
            turns=state.turns,
            reason=StopReason.CANCELLED,
            tool_calls=state.tool_calls,
            usage=state.usage,
        )
        if self._journal is not None:
            with suppress(Exception):
                self._journal.append(event)
        self._publish(event)

    def _persistence_failure(self, state: _RunState) -> AgentResult:
        error = "Agent state could not be persisted."
        self._publish(
            RunStopped(
                run_id=state.run_id,
                turns=state.turns,
                reason=StopReason.PERSISTENCE_ERROR,
                tool_calls=state.tool_calls,
                usage=state.usage,
                error=error,
            )
        )
        return AgentResult(
            run_id=state.run_id,
            messages=tuple(state.messages),
            stop_reason=StopReason.PERSISTENCE_ERROR,
            turns=state.turns,
            tool_calls=state.tool_calls,
            usage=state.usage,
            error=error,
        )

    def _publish(self, event: AgentEvent) -> None:
        try:
            self._events.publish(event)
        except Exception:
            return

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", run_id) is None:
            raise ValueError(
                "run_id must be 1-96 ASCII letters, digits, dots, underscores, or hyphens."
            )
        return run_id


def _bounded_event_error(error: str | None) -> str | None:
    return error[:500] if error is not None else None
