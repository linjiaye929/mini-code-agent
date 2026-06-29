from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from mini_code_agent.domain.content import TextBlock, ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
    ProviderStreamEvent,
    TokenUsage,
)
from mini_code_agent.providers.http import ProviderHttpTransport, decode_json_object

_MODEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_CAPABILITIES: Final = ProviderCapabilities(parallel_tool_calls=True)
_PROTECTED_HEADERS: Final = frozenset({"authorization", "content-type"})


class _OpenAIFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: str = Field(max_length=4 * 1024 * 1024)


class _OpenAIToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    type: Literal["function"]
    function: _OpenAIFunction


class _OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["assistant"]
    content: str | None = None
    tool_calls: tuple[_OpenAIToolCall, ...] = ()
    function_call: object | None = None


class _OpenAIChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: Literal[0]
    message: _OpenAIMessage
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"]


class _OpenAIUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)


class _OpenAIResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: tuple[_OpenAIChoice, ...] = Field(min_length=1, max_length=1)
    usage: _OpenAIUsage | None = None


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        extra_headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key.get_secret_value()
        if not key or len(key) > 4_096 or "\r" in key or "\n" in key:
            raise ValueError("api_key must contain between 1 and 4096 safe characters")
        if not _MODEL_PATTERN.fullmatch(model):
            raise ValueError("model must be a valid provider model identifier")

        self._api_key = key
        self._model = model
        self._extra_headers = _validate_extra_headers(extra_headers or {})
        self._transport = ProviderHttpTransport(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            client=client,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _CAPABILITIES

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload, request_id = await self._transport.post_json(
            "chat/completions",
            headers=self._headers(),
            payload=self._request_payload(request, stream=False),
        )
        return self._parse_response(payload, request_id=request_id)

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ProviderStreamEvent]:
        del request
        raise ProviderError(
            ProviderErrorCode.INVALID_RESPONSE,
            "OpenAI-compatible streaming is not available.",
            retryable=False,
        )
        yield

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
            **self._extra_headers,
        }

    def _request_payload(
        self,
        request: ModelRequest,
        *,
        stream: bool,
    ) -> dict[str, object]:
        messages: list[dict[str, object]] = []
        if request.system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": request.system_prompt,
                }
            )
        for message in request.messages:
            messages.extend(self._message_payloads(message))

        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.model_dump(mode="json")["input_schema"],
                    },
                }
                for tool in request.tools
            ]
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _message_payloads(message: Message) -> list[dict[str, object]]:
        if message.role is MessageRole.USER:
            payloads: list[dict[str, object]] = [
                {
                    "role": "tool",
                    "tool_call_id": block.tool_call_id,
                    "content": block.content,
                }
                for block in message.content
                if isinstance(block, ToolResult)
            ]
            text = message.text
            if text:
                payloads.append({"role": "user", "content": text})
            return payloads

        tool_calls = [
            {
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": _compact_json(block.model_dump(mode="json")["arguments"]),
                },
            }
            for block in message.content
            if isinstance(block, ToolCall)
        ]
        payload: dict[str, object] = {
            "role": "assistant",
            "content": message.text or None,
        }
        if tool_calls:
            payload["tool_calls"] = tool_calls
        return [payload]

    @staticmethod
    def _parse_response(
        payload: Mapping[str, object],
        *,
        request_id: str | None,
    ) -> ModelResponse:
        try:
            wire = _OpenAIResponse.model_validate(payload)
            choice = wire.choices[0]
            if choice.message.function_call is not None:
                raise ValueError("deprecated function_call is unsupported")

            content: list[TextBlock | ToolCall] = []
            if choice.message.content:
                content.append(TextBlock(text=choice.message.content))

            tool_ids: set[str] = set()
            for wire_call in choice.message.tool_calls:
                if wire_call.id in tool_ids:
                    raise ValueError("duplicate tool call id")
                tool_ids.add(wire_call.id)
                content.append(
                    ToolCall(
                        id=wire_call.id,
                        name=wire_call.function.name,
                        arguments=decode_json_object(wire_call.function.arguments.encode("utf-8")),
                    )
                )

            usage = wire.usage
            return ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=tuple(content),
                ),
                finish_reason=_map_finish_reason(choice.finish_reason),
                usage=TokenUsage(
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                ),
                provider_request_id=request_id,
            )
        except ProviderError:
            raise _invalid_openai_response() from None
        except (ValidationError, ValueError, TypeError):
            raise _invalid_openai_response() from None


def _validate_extra_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if len(headers) > 32:
        raise ValueError("extra_headers cannot contain more than 32 entries")
    validated: dict[str, str] = {}
    seen: set[str] = set()
    for name, value in headers.items():
        normalized_name = name.lower()
        if (
            not name
            or len(name) > 128
            or normalized_name in _PROTECTED_HEADERS
            or normalized_name in seen
            or "\r" in name
            or "\n" in name
            or len(value) > 4_096
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError("extra_headers contains an invalid or protected header")
        try:
            httpx.Headers({name: value})
        except (TypeError, ValueError):
            raise ValueError("extra_headers contains an invalid header") from None
        seen.add(normalized_name)
        validated[name] = value
    return validated


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _map_finish_reason(value: str) -> FinishReason:
    if value == "stop":
        return FinishReason.STOP
    if value == "tool_calls":
        return FinishReason.TOOL_CALL
    if value == "length":
        return FinishReason.MAX_TOKENS
    if value == "content_filter":
        return FinishReason.CONTENT_FILTER
    raise ValueError("unsupported OpenAI-compatible finish reason")


def _invalid_openai_response() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.INVALID_RESPONSE,
        "OpenAI-compatible provider returned an invalid response.",
        retryable=False,
    )
