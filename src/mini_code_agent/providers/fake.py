from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Iterable

from mini_code_agent.domain.content import TextBlock, ToolCall
from mini_code_agent.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
    ProviderStreamEvent,
    ResponseCompleted,
    TextDelta,
    ToolCallDelta,
)


class ScriptedProvider:
    def __init__(
        self,
        steps: Iterable[ModelResponse | ProviderError],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._steps = deque(steps)
        self._delay_seconds = delay_seconds
        self.requests: list[ModelRequest] = []
        self._capabilities = ProviderCapabilities()

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if not self._steps:
            raise ProviderError(
                ProviderErrorCode.INVALID_RESPONSE,
                "The scripted provider has no remaining response.",
                retryable=False,
            )
        step = self._steps.popleft()
        if isinstance(step, ProviderError):
            raise step
        return step

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ProviderStreamEvent]:
        response = await self.complete(request)
        for index, block in enumerate(response.message.content):
            if isinstance(block, TextBlock):
                yield TextDelta(text=block.text)
            elif isinstance(block, ToolCall):
                arguments = block.model_dump(mode="json")["arguments"]
                yield ToolCallDelta(
                    index=index,
                    tool_call_id=block.id,
                    name=block.name,
                    partial_json=json.dumps(
                        arguments,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
        yield ResponseCompleted(response=response)
