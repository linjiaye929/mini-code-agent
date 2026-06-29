from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, cast

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    ValidationError,
)

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
    ResponseCompleted,
    TextDelta,
    TokenUsage,
    ToolCallDelta,
)
from mini_code_agent.providers.http import (
    JsonObject,
    ProviderHttpTransport,
    decode_json_object,
)

_MODEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_CAPABILITIES: Final = ProviderCapabilities(parallel_tool_calls=True)
_PROTECTED_HEADERS: Final = frozenset({"authorization", "content-type"})


def _empty_string_list() -> list[str]:
    return []


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


@dataclass(slots=True)
class _OpenAIToolStreamState:
    index: int
    tool_call_id: str
    name: str
    fragments: list[str] = field(default_factory=_empty_string_list)


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
        parser = _OpenAIStreamParser()
        async with self._transport.stream_sse(
            "chat/completions",
            headers=self._headers(),
            payload=self._request_payload(request, stream=True),
        ) as connection:
            async for event in connection.events:
                try:
                    normalized_events = parser.consume(event.data)
                except ProviderError:
                    raise
                except (TypeError, ValueError, ValidationError):
                    raise _invalid_openai_response() from None
                for normalized in normalized_events:
                    yield normalized
            try:
                response = parser.complete(request_id=connection.request_id)
            except ProviderError:
                raise
            except (TypeError, ValueError, ValidationError):
                raise _invalid_openai_response() from None
        yield ResponseCompleted(response=response)

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
                    "content": _tool_result_content(block),
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


class _OpenAIStreamParser:
    def __init__(self) -> None:
        self._saw_chunk = False
        self._done = False
        self._finish_reason: FinishReason | None = None
        self._text_fragments: list[str] = []
        self._tools: dict[int, _OpenAIToolStreamState] = {}
        self._tool_ids: set[str] = set()
        self._usage = TokenUsage()

    def consume(
        self,
        data: str,
    ) -> tuple[TextDelta | ToolCallDelta, ...]:
        if self._done:
            raise ValueError("event received after [DONE]")
        if data.strip() == "[DONE]":
            if self._finish_reason is None:
                raise ValueError("[DONE] received before finish reason")
            self._done = True
            return ()

        payload = decode_json_object(data.encode("utf-8"))
        if "error" in payload:
            raise _stream_error(payload)
        choices = payload.get("choices")
        if not isinstance(choices, list):
            raise ValueError("stream chunk choices must be an array")

        usage_value = payload.get("usage")
        if usage_value is not None:
            self._usage = _parse_stream_usage(usage_value)

        if not choices:
            if usage_value is None or self._finish_reason is None:
                raise ValueError("empty choices are valid only for final usage")
            return ()
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("stream chunk must contain exactly one choice")
        if self._finish_reason is not None:
            raise ValueError("choice received after finish reason")

        choice = choices[0]
        if _required_nonnegative_int(choice, "index") != 0:
            raise ValueError("only choice index zero is supported")
        delta = _required_object(choice, "delta")
        role = delta.get("role")
        if role is not None and role != "assistant":
            raise ValueError("streamed role must be assistant")

        normalized: list[TextDelta | ToolCallDelta] = []
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise ValueError("streamed content must be text")
            if content:
                self._text_fragments.append(content)
                normalized.append(TextDelta(text=content))

        tool_calls = delta.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise ValueError("streamed tool_calls must be an array")
            for wire_call in tool_calls:
                if not isinstance(wire_call, dict):
                    raise ValueError("streamed tool call must be an object")
                tool_event = self._consume_tool_call(wire_call)
                if tool_event is not None:
                    normalized.append(tool_event)

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            if not isinstance(finish_reason, str):
                raise ValueError("finish_reason must be a string")
            self._finish_reason = _map_finish_reason(finish_reason)

        self._saw_chunk = True
        return tuple(normalized)

    def complete(self, *, request_id: str | None) -> ModelResponse:
        if not self._saw_chunk or not self._done or self._finish_reason is None:
            raise ValueError("stream ended before completion")
        indexes = sorted(self._tools)
        if indexes != list(range(len(indexes))):
            raise ValueError("tool call indexes are not contiguous")

        content: list[TextBlock | ToolCall] = []
        text = "".join(self._text_fragments)
        if text:
            content.append(TextBlock(text=text))
        for index in indexes:
            state = self._tools[index]
            arguments = decode_json_object(("".join(state.fragments) or "{}").encode("utf-8"))
            content.append(
                ToolCall(
                    id=state.tool_call_id,
                    name=state.name,
                    arguments=arguments,
                )
            )

        return ModelResponse(
            message=Message(
                role=MessageRole.ASSISTANT,
                content=tuple(content),
            ),
            finish_reason=self._finish_reason,
            usage=self._usage,
            provider_request_id=request_id,
        )

    def _consume_tool_call(
        self,
        wire_call: JsonObject,
    ) -> ToolCallDelta | None:
        index = _required_nonnegative_int(wire_call, "index")
        if index > 255:
            raise ValueError("tool call index exceeds limit")
        state = self._tools.get(index)
        wire_id = wire_call.get("id")
        wire_type = wire_call.get("type")
        function = _required_object(wire_call, "function")
        wire_name = function.get("name")

        if state is None:
            if len(self._tools) >= 256:
                raise ValueError("too many tool calls")
            if not isinstance(wire_id, str) or not 0 < len(wire_id) <= 128:
                raise ValueError("first tool chunk requires a bounded id")
            if wire_type != "function":
                raise ValueError("first tool chunk must be a function")
            if not isinstance(wire_name, str) or not 0 < len(wire_name) <= 64:
                raise ValueError("first tool chunk requires a bounded name")
            if wire_id in self._tool_ids:
                raise ValueError("duplicate tool call id")
            self._tool_ids.add(wire_id)
            state = _OpenAIToolStreamState(
                index=index,
                tool_call_id=wire_id,
                name=wire_name,
            )
            self._tools[index] = state
        else:
            if wire_id is not None and wire_id != state.tool_call_id:
                raise ValueError("tool call id changed during streaming")
            if wire_type is not None and wire_type != "function":
                raise ValueError("tool call type changed during streaming")
            if wire_name is not None and wire_name != state.name:
                raise ValueError("tool call name changed during streaming")

        arguments = function.get("arguments")
        if arguments is None:
            return None
        if not isinstance(arguments, str):
            raise ValueError("tool arguments fragment must be text")
        if not arguments:
            return None
        state.fragments.append(arguments)
        return ToolCallDelta(
            index=index,
            tool_call_id=state.tool_call_id,
            name=state.name,
            partial_json=arguments,
        )


def _validate_extra_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if len(headers) > 32:
        raise ValueError("extra_headers cannot contain more than 32 entries")
    validated: dict[str, str] = {}
    seen: set[str] = set()
    raw_headers = cast(Mapping[object, object], headers)
    for name, value in raw_headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("extra_headers names and values must be strings")
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


def _tool_result_content(result: ToolResult) -> str:
    if not result.is_error:
        return result.content
    return _compact_json(
        {
            "content": result.content,
            "is_error": True,
        }
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


def _required_object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _required_nonnegative_int(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _parse_stream_usage(value: JsonValue) -> TokenUsage:
    if not isinstance(value, dict):
        raise ValueError("usage must be an object")
    return TokenUsage(
        input_tokens=_required_nonnegative_int(value, "prompt_tokens"),
        output_tokens=_required_nonnegative_int(value, "completion_tokens"),
    )


def _stream_error(payload: JsonObject) -> ProviderError:
    error = _required_object(payload, "error")
    error_type = error.get("type") or error.get("code")
    if not isinstance(error_type, str):
        return _invalid_openai_response()
    if error_type in {
        "authentication_error",
        "invalid_api_key",
        "permission_error",
    }:
        return ProviderError(
            ProviderErrorCode.AUTHENTICATION,
            "OpenAI-compatible stream authentication failed.",
            retryable=False,
        )
    if error_type in {"rate_limit_error", "rate_limit_exceeded"}:
        return ProviderError(
            ProviderErrorCode.RATE_LIMIT,
            "OpenAI-compatible stream was rate limited.",
            retryable=True,
        )
    if error_type in {"timeout", "timeout_error"}:
        return ProviderError(
            ProviderErrorCode.TIMEOUT,
            "OpenAI-compatible stream timed out.",
            retryable=True,
        )
    if error_type in {"server_error", "api_error", "overloaded_error"}:
        return ProviderError(
            ProviderErrorCode.SERVER,
            "OpenAI-compatible stream failed temporarily.",
            retryable=True,
        )
    return _invalid_openai_response()


def _invalid_openai_response() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.INVALID_RESPONSE,
        "OpenAI-compatible provider returned an invalid response.",
        retryable=False,
    )
