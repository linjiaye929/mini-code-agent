from __future__ import annotations

import json
from typing import Protocol

from mini_code_agent.domain.messages import Message
from mini_code_agent.tools.base import ToolDefinition

_REQUEST_FRAMING_TOKENS = 16
_MESSAGE_FRAMING_TOKENS = 8
_TOOL_FRAMING_TOKENS = 8


class TokenEstimator(Protocol):
    def estimate(
        self,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> int: ...


class Utf8TokenEstimator:
    def estimate(
        self,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> int:
        payload = {
            "messages": [message.model_dump(mode="json") for message in messages],
            "system_prompt": system_prompt,
            "tools": [tool.model_dump(mode="json") for tool in tools],
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return (
            len(canonical)
            + _REQUEST_FRAMING_TOKENS
            + len(messages) * _MESSAGE_FRAMING_TOKENS
            + len(tools) * _TOOL_FRAMING_TOKENS
        )
