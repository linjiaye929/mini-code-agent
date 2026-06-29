from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from mini_code_agent.agent.events import (
    EventSink,
    ModelCompleted,
    NullEventSink,
    RunStarted,
    RunStopped,
    ToolCompleted,
)
from mini_code_agent.agent.models import AgentLimits, AgentResult, StopReason
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ProviderError,
    TokenUsage,
)
from mini_code_agent.tools.base import ToolExecutor


class AgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolExecutor,
        *,
        limits: AgentLimits | None = None,
        events: EventSink | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._limits = limits or AgentLimits()
        self._events = events or NullEventSink()

    async def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        run_id: str | None = None,
    ) -> AgentResult:
        active_run_id = run_id or str(uuid4())
        messages = [Message.user_text(user_prompt)]
        usage = TokenUsage()
        seen_call_ids: set[str] = set()
        tool_call_count = 0
        self._events.publish(RunStarted(run_id=active_run_id, max_turns=self._limits.max_turns))

        for turn in range(1, self._limits.max_turns + 1):
            request = ModelRequest(
                request_id=f"{active_run_id}:{turn}",
                system_prompt=system_prompt,
                messages=tuple(messages),
                tools=self._tools.definitions,
            )
            try:
                async with asyncio.timeout(self._limits.provider_timeout_seconds):
                    response = await self._provider.complete(request)
            except asyncio.CancelledError:
                self._events.publish(
                    RunStopped(
                        run_id=active_run_id,
                        turns=turn - 1,
                        reason=StopReason.CANCELLED,
                    )
                )
                raise
            except TimeoutError:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_TIMEOUT,
                    turn - 1,
                    tool_call_count,
                    usage,
                    "Provider request timed out.",
                )
            except ProviderError as exc:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_ERROR,
                    turn - 1,
                    tool_call_count,
                    usage,
                    exc.public_message,
                )
            except Exception:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_ERROR,
                    turn - 1,
                    tool_call_count,
                    usage,
                    "Provider request failed unexpectedly.",
                )

            messages.append(response.message)
            usage = TokenUsage(
                input_tokens=usage.input_tokens + response.usage.input_tokens,
                output_tokens=usage.output_tokens + response.usage.output_tokens,
            )
            self._events.publish(
                ModelCompleted(
                    run_id=active_run_id,
                    turn=turn,
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                )
            )

            if response.finish_reason is FinishReason.STOP:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.COMPLETED,
                    turn,
                    tool_call_count,
                    usage,
                    final_text=response.message.text,
                )

            if response.finish_reason is not FinishReason.TOOL_CALL:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_LIMIT,
                    turn,
                    tool_call_count,
                    usage,
                    "Provider stopped before completing the response.",
                )

            tool_results: list[ToolResult] = []
            for call in response.message.tool_calls:
                if call.id in seen_call_ids:
                    return self._stop(
                        active_run_id,
                        messages,
                        StopReason.DUPLICATE_TOOL_CALL,
                        turn,
                        tool_call_count,
                        usage,
                        "Provider repeated a ToolCall identifier.",
                    )
                if tool_call_count >= self._limits.max_tool_calls:
                    return self._stop(
                        active_run_id,
                        messages,
                        StopReason.MAX_TOOL_CALLS,
                        turn,
                        tool_call_count,
                        usage,
                        "Agent reached the ToolCall limit.",
                    )
                seen_call_ids.add(call.id)
                tool_call_count += 1
                result = await self._execute_tool(call)
                tool_results.append(result)
                self._events.publish(
                    ToolCompleted(
                        run_id=active_run_id,
                        turn=turn,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        is_error=result.is_error,
                    )
                )
            messages.append(Message(role=MessageRole.USER, content=tuple(tool_results)))

        return self._stop(
            active_run_id,
            messages,
            StopReason.MAX_TURNS,
            self._limits.max_turns,
            tool_call_count,
            usage,
            "Agent reached the turn limit.",
        )

    async def _execute_tool(self, call: ToolCall) -> ToolResult:
        try:
            async with asyncio.timeout(self._limits.tool_timeout_seconds):
                result = await self._tools.execute(call)
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
        run_id: str,
        messages: list[Message],
        reason: StopReason,
        turns: int,
        tool_calls: int,
        usage: TokenUsage,
        error: str | None = None,
        *,
        final_text: str | None = None,
    ) -> AgentResult:
        self._events.publish(
            RunStopped(
                run_id=run_id,
                turns=turns,
                reason=reason,
                error=error,
            )
        )
        return AgentResult(
            run_id=run_id,
            messages=tuple(messages),
            stop_reason=reason,
            turns=turns,
            tool_calls=tool_calls,
            usage=usage,
            final_text=final_text,
            error=error,
        )
