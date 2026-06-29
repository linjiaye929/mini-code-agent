from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Final, Literal

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

_MODEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_VERSION_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CAPABILITIES: Final = ProviderCapabilities(parallel_tool_calls=True)


def _empty_string_list() -> list[str]:
    return []


class _AnthropicTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(min_length=1)


class _AnthropicToolUseBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_use"]
    id: str = Field(min_length=1, max_length=128)
    name: str
    input: dict[str, JsonValue]


type _AnthropicContentBlock = Annotated[
    _AnthropicTextBlock | _AnthropicToolUseBlock,
    Field(discriminator="type"),
]


class _AnthropicUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class _AnthropicMessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=256)
    type: Literal["message"]
    role: Literal["assistant"]
    content: tuple[_AnthropicContentBlock, ...] = Field(min_length=1)
    stop_reason: Literal[
        "end_turn",
        "max_tokens",
        "stop_sequence",
        "tool_use",
        "pause_turn",
        "refusal",
        "model_context_window_exceeded",
    ]
    usage: _AnthropicUsage


@dataclass(slots=True)
class _TextStreamBlock:
    index: int
    fragments: list[str] = field(default_factory=_empty_string_list)
    closed: bool = False
    domain_block: TextBlock | None = None


@dataclass(slots=True)
class _ToolStreamBlock:
    index: int
    tool_call_id: str
    name: str
    fragments: list[str] = field(default_factory=_empty_string_list)
    closed: bool = False
    domain_block: ToolCall | None = None


type _StreamBlock = _TextStreamBlock | _ToolStreamBlock


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        max_tokens: int = 4096,
        base_url: str = "https://api.anthropic.com",
        anthropic_version: str = "2023-06-01",
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key.get_secret_value()
        if not key or len(key) > 4_096 or "\r" in key or "\n" in key:
            raise ValueError("api_key must contain between 1 and 4096 safe characters")
        if not _MODEL_PATTERN.fullmatch(model):
            raise ValueError("model must be a valid provider model identifier")
        if not 1 <= max_tokens <= 1_000_000:
            raise ValueError("max_tokens must be between 1 and 1000000")
        if not _VERSION_PATTERN.fullmatch(anthropic_version):
            raise ValueError("anthropic_version must use YYYY-MM-DD format")

        self._api_key = key
        self._model = model
        self._max_tokens = max_tokens
        self._anthropic_version = anthropic_version
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
            "v1/messages",
            headers=self._headers(),
            payload=self._request_payload(request, stream=False),
        )
        return self._parse_response(payload, request_id=request_id)

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ProviderStreamEvent]:
        parser = _AnthropicStreamParser()
        async with self._transport.stream_sse(
            "v1/messages",
            headers=self._headers(),
            payload=self._request_payload(request, stream=True),
        ) as connection:
            async for event in connection.events:
                try:
                    payload = decode_json_object(event.data.encode("utf-8"))
                    normalized_events = parser.consume(event.event, payload)
                except ProviderError:
                    raise
                except (TypeError, ValueError, ValidationError):
                    raise _invalid_anthropic_response() from None
                for normalized in normalized_events:
                    yield normalized
            try:
                response = parser.complete(request_id=connection.request_id)
            except ProviderError:
                raise
            except (TypeError, ValueError, ValidationError):
                raise _invalid_anthropic_response() from None
        yield ResponseCompleted(response=response)

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._anthropic_version,
            "content-type": "application/json",
        }

    def _request_payload(
        self,
        request: ModelRequest,
        *,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [self._message_payload(message) for message in request.messages],
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.model_dump(mode="json")["input_schema"],
                }
                for tool in request.tools
            ]
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    def _message_payload(message: Message) -> dict[str, object]:
        if message.role is MessageRole.USER:
            result_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_call_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
                for block in message.content
                if isinstance(block, ToolResult)
            ]
            text_blocks = [
                {"type": "text", "text": block.text}
                for block in message.content
                if isinstance(block, TextBlock)
            ]
            return {
                "role": "user",
                "content": [*result_blocks, *text_blocks],
            }

        content: list[dict[str, object]] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolCall):
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.model_dump(mode="json")["arguments"],
                    }
                )
        return {"role": "assistant", "content": content}

    @staticmethod
    def _parse_response(
        payload: dict[str, JsonValue],
        *,
        request_id: str | None,
    ) -> ModelResponse:
        try:
            wire = _AnthropicMessageResponse.model_validate(payload)
            if wire.stop_reason == "pause_turn":
                raise ValueError("pause_turn cannot be represented losslessly")

            content: list[TextBlock | ToolCall] = []
            tool_ids: set[str] = set()
            for block in wire.content:
                if isinstance(block, _AnthropicTextBlock):
                    content.append(TextBlock(text=block.text))
                    continue
                if block.id in tool_ids:
                    raise ValueError("duplicate tool call id")
                tool_ids.add(block.id)
                content.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

            finish_reason = _map_finish_reason(wire.stop_reason)
            return ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=tuple(content),
                ),
                finish_reason=finish_reason,
                usage=TokenUsage(
                    input_tokens=wire.usage.input_tokens,
                    output_tokens=wire.usage.output_tokens,
                ),
                provider_request_id=request_id,
            )
        except (ValidationError, ValueError, TypeError):
            raise _invalid_anthropic_response() from None


class _AnthropicStreamParser:
    def __init__(self) -> None:
        self._started = False
        self._stopped = False
        self._blocks: dict[int, _StreamBlock] = {}
        self._tool_ids: set[str] = set()
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._finish_reason: FinishReason | None = None

    def consume(
        self,
        event_name: str,
        payload: JsonObject,
    ) -> tuple[TextDelta | ToolCallDelta, ...]:
        payload_type = _required_string(payload, "type")
        if event_name != payload_type:
            raise ValueError("SSE event name and payload type differ")
        if self._stopped:
            raise ValueError("event received after message_stop")
        if payload_type == "error":
            raise _stream_error(payload)
        if payload_type in {"ping", "future_event"}:
            return ()
        if payload_type not in {
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        }:
            return ()
        if payload_type != "message_start" and not self._started:
            raise ValueError("stream did not start with message_start")

        if payload_type == "message_start":
            self._consume_message_start(payload)
            return ()
        if payload_type == "content_block_start":
            self._consume_content_block_start(payload)
            return ()
        if payload_type == "content_block_delta":
            event = self._consume_content_block_delta(payload)
            return () if event is None else (event,)
        if payload_type == "content_block_stop":
            self._consume_content_block_stop(payload)
            return ()
        if payload_type == "message_delta":
            self._consume_message_delta(payload)
            return ()

        self._consume_message_stop()
        return ()

    def complete(self, *, request_id: str | None) -> ModelResponse:
        if not self._stopped:
            raise ValueError("stream ended before message_stop")
        if self._input_tokens is None or self._output_tokens is None or self._finish_reason is None:
            raise ValueError("stream completion metadata is incomplete")
        indexes = sorted(self._blocks)
        if indexes != list(range(len(indexes))):
            raise ValueError("content block indexes are not contiguous")

        content: list[TextBlock | ToolCall] = []
        for index in indexes:
            block = self._blocks[index]
            if not block.closed or block.domain_block is None:
                raise ValueError("content block was not completed")
            content.append(block.domain_block)

        return ModelResponse(
            message=Message(
                role=MessageRole.ASSISTANT,
                content=tuple(content),
            ),
            finish_reason=self._finish_reason,
            usage=TokenUsage(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            ),
            provider_request_id=request_id,
        )

    def _consume_message_start(self, payload: JsonObject) -> None:
        if self._started:
            raise ValueError("duplicate message_start")
        message = _required_object(payload, "message")
        if (
            _required_string(message, "type") != "message"
            or _required_string(message, "role") != "assistant"
        ):
            raise ValueError("invalid streamed message")
        content = message.get("content")
        if not isinstance(content, list) or content:
            raise ValueError("message_start content must be empty")
        usage = _required_object(message, "usage")
        self._input_tokens = _required_nonnegative_int(usage, "input_tokens")
        self._output_tokens = _required_nonnegative_int(usage, "output_tokens")
        self._started = True

    def _consume_content_block_start(self, payload: JsonObject) -> None:
        if self._finish_reason is not None:
            raise ValueError("content block started after message_delta")
        index = _required_index(payload)
        if index in self._blocks or len(self._blocks) >= 256:
            raise ValueError("duplicate or excessive content block")
        wire_block = _required_object(payload, "content_block")
        block_type = _required_string(wire_block, "type")
        if block_type == "text":
            initial_text = _required_string(wire_block, "text")
            block = _TextStreamBlock(index=index)
            if initial_text:
                block.fragments.append(initial_text)
            self._blocks[index] = block
            return
        if block_type != "tool_use":
            raise ValueError("unsupported streamed content block")

        tool_call_id = _bounded_string(wire_block, "id", maximum=128)
        if tool_call_id in self._tool_ids:
            raise ValueError("duplicate tool call id")
        name = _bounded_string(wire_block, "name", maximum=64)
        initial_input = _required_object(wire_block, "input")
        if initial_input:
            raise ValueError("non-empty initial tool input is unsupported")
        self._tool_ids.add(tool_call_id)
        self._blocks[index] = _ToolStreamBlock(
            index=index,
            tool_call_id=tool_call_id,
            name=name,
        )

    def _consume_content_block_delta(
        self,
        payload: JsonObject,
    ) -> TextDelta | ToolCallDelta | None:
        index = _required_index(payload)
        block = self._required_open_block(index)
        delta = _required_object(payload, "delta")
        delta_type = _required_string(delta, "type")
        if isinstance(block, _TextStreamBlock):
            if delta_type != "text_delta":
                raise ValueError("text block received a non-text delta")
            text = _required_string(delta, "text")
            if not text:
                return None
            block.fragments.append(text)
            return TextDelta(text=text)

        if delta_type != "input_json_delta":
            raise ValueError("tool block received a non-input delta")
        partial_json = _required_string(delta, "partial_json")
        if not partial_json:
            return None
        block.fragments.append(partial_json)
        return ToolCallDelta(
            index=index,
            tool_call_id=block.tool_call_id,
            name=block.name,
            partial_json=partial_json,
        )

    def _consume_content_block_stop(self, payload: JsonObject) -> None:
        index = _required_index(payload)
        block = self._required_open_block(index)
        if isinstance(block, _TextStreamBlock):
            text = "".join(block.fragments)
            block.domain_block = TextBlock(text=text)
        else:
            encoded_arguments = "".join(block.fragments) or "{}"
            arguments = decode_json_object(encoded_arguments.encode("utf-8"))
            block.domain_block = ToolCall(
                id=block.tool_call_id,
                name=block.name,
                arguments=arguments,
            )
        block.closed = True

    def _consume_message_delta(self, payload: JsonObject) -> None:
        if any(not block.closed for block in self._blocks.values()):
            raise ValueError("message_delta received before content blocks closed")
        delta = _required_object(payload, "delta")
        stop_reason = _required_string(delta, "stop_reason")
        finish_reason = _map_finish_reason(stop_reason)
        if self._finish_reason is not None and self._finish_reason is not finish_reason:
            raise ValueError("stop reason changed during stream")
        self._finish_reason = finish_reason
        usage = _required_object(payload, "usage")
        self._output_tokens = _required_nonnegative_int(usage, "output_tokens")

    def _consume_message_stop(self) -> None:
        if self._finish_reason is None:
            raise ValueError("message_stop received before stop reason")
        if any(not block.closed for block in self._blocks.values()):
            raise ValueError("message_stop received before content blocks closed")
        self._stopped = True

    def _required_open_block(self, index: int) -> _StreamBlock:
        block = self._blocks.get(index)
        if block is None or block.closed:
            raise ValueError("content block is absent or already closed")
        return block


def _map_finish_reason(value: str) -> FinishReason:
    if value in {"end_turn", "stop_sequence"}:
        return FinishReason.STOP
    if value == "tool_use":
        return FinishReason.TOOL_CALL
    if value in {"max_tokens", "model_context_window_exceeded"}:
        return FinishReason.MAX_TOKENS
    if value == "refusal":
        return FinishReason.CONTENT_FILTER
    raise ValueError("unsupported Anthropic stop reason")


def _required_object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _required_string(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _bounded_string(
    payload: JsonObject,
    key: str,
    *,
    maximum: int,
) -> str:
    value = _required_string(payload, key)
    if not value or len(value) > maximum:
        raise ValueError(f"{key} has an invalid length")
    return value


def _required_nonnegative_int(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _required_index(payload: JsonObject) -> int:
    index = _required_nonnegative_int(payload, "index")
    if index > 255:
        raise ValueError("content block index exceeds limit")
    return index


def _stream_error(payload: JsonObject) -> ProviderError:
    error = _required_object(payload, "error")
    error_type = _required_string(error, "type")
    if error_type in {"authentication_error", "permission_error"}:
        return ProviderError(
            ProviderErrorCode.AUTHENTICATION,
            "Anthropic authentication failed during streaming.",
            retryable=False,
        )
    if error_type == "rate_limit_error":
        return ProviderError(
            ProviderErrorCode.RATE_LIMIT,
            "Anthropic stream was rate limited.",
            retryable=True,
        )
    if error_type == "timeout_error":
        return ProviderError(
            ProviderErrorCode.TIMEOUT,
            "Anthropic stream timed out.",
            retryable=True,
        )
    if error_type in {"api_error", "overloaded_error"}:
        return ProviderError(
            ProviderErrorCode.SERVER,
            "Anthropic stream failed temporarily.",
            retryable=True,
        )
    return _invalid_anthropic_response()


def _invalid_anthropic_response() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.INVALID_RESPONSE,
        "Anthropic returned an invalid response.",
        retryable=False,
    )
