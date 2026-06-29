from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from mini_code_agent.domain.content import TextBlock, ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.anthropic import AnthropicProvider
from mini_code_agent.providers.base import (
    FinishReason,
    ModelRequest,
    ProviderError,
    ProviderErrorCode,
    ResponseCompleted,
    TextDelta,
    TokenUsage,
    ToolCallDelta,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition


def runtime_tool() -> ToolDefinition:
    return ToolDefinition(
        name="runtime_info",
        description="Return runtime metadata.",
        input_schema={
            "type": "object",
            "properties": {"verbose": {"type": "boolean"}},
        },
        side_effect=SideEffect.READ_ONLY,
    )


def tool_round_trip_request() -> ModelRequest:
    return ModelRequest(
        request_id="local-request-1",
        system_prompt="Work carefully.",
        messages=(
            Message.user_text("Inspect."),
            Message(
                role=MessageRole.ASSISTANT,
                content=(
                    ToolCall(
                        id="call-1",
                        name="runtime_info",
                        arguments={},
                    ),
                ),
            ),
            Message(
                role=MessageRole.USER,
                content=(
                    TextBlock(text="Continue after the result."),
                    ToolResult(
                        tool_call_id="call-1",
                        content="{}",
                    ),
                ),
            ),
        ),
        tools=(runtime_tool(),),
    )


def anthropic_response(
    *,
    content: list[dict[str, Any]] | None = None,
    stop_reason: str = "end_turn",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": content if content is not None else [{"type": "text", "text": "done"}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 12, "output_tokens": 7},
    }


def provider_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AnthropicProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        api_key=SecretStr("test-key"),
        model="claude-test",
        max_tokens=1024,
        base_url="https://provider.test",
        client=client,
    )
    return provider, client


@pytest.mark.asyncio
async def test_complete_converts_domain_request_to_anthropic_messages() -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://provider.test/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["content-type"] == "application/json"
        captured_body.update(cast(dict[str, Any], json.loads(request.content)))
        return httpx.Response(
            200,
            json=anthropic_response(),
            headers={"request-id": "req_1"},
            request=request,
        )

    provider, client = provider_with_handler(handler)

    result = await provider.complete(tool_round_trip_request())

    assert captured_body == {
        "model": "claude-test",
        "max_tokens": 1024,
        "system": "Work carefully.",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Inspect."}],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "runtime_info",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "{}",
                        "is_error": False,
                    },
                    {
                        "type": "text",
                        "text": "Continue after the result.",
                    },
                ],
            },
        ],
        "tools": [
            {
                "name": "runtime_info",
                "description": "Return runtime metadata.",
                "input_schema": {
                    "type": "object",
                    "properties": {"verbose": {"type": "boolean"}},
                },
            }
        ],
    }
    assert result.message.text == "done"
    assert result.finish_reason is FinishReason.STOP
    assert result.usage == TokenUsage(input_tokens=12, output_tokens=7)
    assert result.provider_request_id == "req_1"
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_omits_empty_system_and_tools() -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(cast(dict[str, Any], json.loads(request.content)))
        return httpx.Response(200, json=anthropic_response(), request=request)

    provider, client = provider_with_handler(handler)
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("hello"),),
    )

    await provider.complete(request)

    assert "system" not in captured_body
    assert "tools" not in captured_body
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_normalizes_text_and_parallel_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=anthropic_response(
                content=[
                    {"type": "text", "text": "Checking."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "runtime_info",
                        "input": {"verbose": True},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "runtime_info",
                        "input": {"verbose": False},
                    },
                ],
                stop_reason="tool_use",
                usage={
                    "input_tokens": 12,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 4,
                },
            ),
            request=request,
        )

    provider, client = provider_with_handler(handler)

    result = await provider.complete(
        ModelRequest(
            request_id="request-1",
            system_prompt="",
            messages=(Message.user_text("inspect"),),
            tools=(runtime_tool(),),
        )
    )

    assert result.finish_reason is FinishReason.TOOL_CALL
    assert result.message.text == "Checking."
    assert result.message.tool_calls == (
        ToolCall(
            id="toolu_1",
            name="runtime_info",
            arguments={"verbose": True},
        ),
        ToolCall(
            id="toolu_2",
            name="runtime_info",
            arguments={"verbose": False},
        ),
    )
    assert provider.capabilities.parallel_tool_calls is True
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", FinishReason.STOP),
        ("stop_sequence", FinishReason.STOP),
        ("max_tokens", FinishReason.MAX_TOKENS),
        ("model_context_window_exceeded", FinishReason.MAX_TOKENS),
        ("refusal", FinishReason.CONTENT_FILTER),
    ],
)
async def test_complete_maps_supported_stop_reasons(
    stop_reason: str,
    expected: FinishReason,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=anthropic_response(stop_reason=stop_reason),
            request=request,
        )

    provider, client = provider_with_handler(handler)
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("hello"),),
    )

    result = await provider.complete(request)

    assert result.finish_reason is expected
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_body",
    [
        anthropic_response(content=[], stop_reason="end_turn"),
        anthropic_response(
            content=[{"type": "thinking", "thinking": "hidden"}],
            stop_reason="end_turn",
        ),
        anthropic_response(
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "runtime_info",
                    "input": [],
                }
            ],
            stop_reason="tool_use",
        ),
        anthropic_response(
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "runtime_info",
                    "input": {},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "runtime_info",
                    "input": {},
                },
            ],
            stop_reason="tool_use",
        ),
        anthropic_response(
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "runtime_info",
                    "input": {},
                }
            ],
            stop_reason="end_turn",
        ),
        anthropic_response(stop_reason="pause_turn"),
        {**anthropic_response(), "role": "user"},
        {**anthropic_response(), "usage": {"input_tokens": -1, "output_tokens": 2}},
    ],
)
async def test_complete_rejects_malformed_or_lossy_response(
    response_body: dict[str, Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body, request=request)

    provider, client = provider_with_handler(handler)
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("hello"),),
    )

    with pytest.raises(ProviderError) as captured:
        await provider.complete(request)

    assert captured.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert captured.value.retryable is False
    await client.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": ""},
        {"model": "x" * 257},
        {"model": "bad model"},
        {"max_tokens": 0},
        {"max_tokens": 1_000_001},
        {"anthropic_version": "latest"},
    ],
)
def test_provider_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    arguments: dict[str, object] = {
        "api_key": SecretStr("test-key"),
        "model": "claude-test",
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError):
        AnthropicProvider(**arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provider_close_does_not_close_borrowed_client() -> None:
    provider, client = provider_with_handler(
        lambda request: httpx.Response(200, json=anthropic_response(), request=request)
    )

    await provider.aclose()

    assert client.is_closed is False
    await client.aclose()


def encode_sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    chunks = [
        f"event: {event_name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for event_name, data in events
    ]
    return "".join(chunks).encode()


def anthropic_stream_events() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Checking"},
            },
        ),
        (
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "runtime_info",
                    "input": {},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"verbose":',
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": "true}",
                },
            },
        ),
        (
            "content_block_stop",
            {"type": "content_block_stop", "index": 1},
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 7},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def streaming_provider(
    events: list[tuple[str, dict[str, Any]]],
    *,
    content_type: str = "text/event-stream",
) -> tuple[AnthropicProvider, httpx.AsyncClient, dict[str, Any]]:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(cast(dict[str, Any], json.loads(request.content)))
        return httpx.Response(
            200,
            content=encode_sse(events),
            headers={
                "content-type": content_type,
                "request-id": "req_stream_1",
            },
            request=request,
        )

    provider, client = provider_with_handler(handler)
    return provider, client, captured_body


@pytest.mark.asyncio
async def test_stream_normalizes_text_tool_fragments_usage_and_completion() -> None:
    provider, client, captured_body = streaming_provider(anthropic_stream_events())
    request = ModelRequest(
        request_id="request-1",
        system_prompt="Work carefully.",
        messages=(Message.user_text("inspect"),),
        tools=(runtime_tool(),),
    )

    events = [event async for event in provider.stream(request)]

    assert captured_body["stream"] is True
    assert events[:3] == [
        TextDelta(text="Checking"),
        ToolCallDelta(
            index=1,
            tool_call_id="toolu_1",
            name="runtime_info",
            partial_json='{"verbose":',
        ),
        ToolCallDelta(
            index=1,
            tool_call_id="toolu_1",
            name="runtime_info",
            partial_json="true}",
        ),
    ]
    completed = cast(ResponseCompleted, events[3])
    assert completed.response.finish_reason is FinishReason.TOOL_CALL
    assert completed.response.message.text == "Checking"
    assert completed.response.message.tool_calls == (
        ToolCall(
            id="toolu_1",
            name="runtime_info",
            arguments={"verbose": True},
        ),
    )
    assert completed.response.usage == TokenUsage(input_tokens=10, output_tokens=7)
    assert completed.response.provider_request_id == "req_stream_1"
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_ignores_ping_and_unknown_top_level_events() -> None:
    wire_events = anthropic_stream_events()
    wire_events.insert(1, ("ping", {"type": "ping"}))
    wire_events.insert(2, ("future_event", {"type": "future_event", "value": 1}))
    provider, client, _ = streaming_provider(wire_events)

    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                request_id="request-1",
                system_prompt="",
                messages=(Message.user_text("inspect"),),
            )
        )
    ]

    assert isinstance(events[-1], ResponseCompleted)
    await client.aclose()


def mutate_event(
    events: list[tuple[str, dict[str, Any]]],
    event_name: str,
    mutation: Callable[[dict[str, Any]], None],
) -> list[tuple[str, dict[str, Any]]]:
    copied = [(name, json.loads(json.dumps(data))) for name, data in events]
    for name, data in copied:
        if name == event_name:
            mutation(data)
            return copied
    raise AssertionError(f"event {event_name} not found")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire_events",
    cast(
        list[list[tuple[str, dict[str, Any]]]],
        [
            anthropic_stream_events()[1:],
            anthropic_stream_events()[:-1],
            mutate_event(
                anthropic_stream_events(),
                "message_start",
                lambda data: data["message"]["usage"].update({"input_tokens": -1}),
            ),
            mutate_event(
                anthropic_stream_events(),
                "content_block_start",
                lambda data: data.update({"index": 2}),
            ),
            mutate_event(
                anthropic_stream_events(),
                "content_block_delta",
                lambda data: data.update({"index": 9}),
            ),
            mutate_event(
                anthropic_stream_events(),
                "message_delta",
                lambda data: data["delta"].update({"stop_reason": "pause_turn"}),
            ),
            [
                *anthropic_stream_events()[:-1],
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "runtime_info",
                            "input": {},
                        },
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ],
        ],
    ),
)
async def test_stream_rejects_invalid_lifecycle(
    wire_events: list[tuple[str, dict[str, Any]]],
) -> None:
    provider, client, _ = streaming_provider(wire_events)
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("inspect"),),
    )

    with pytest.raises(ProviderError) as captured:
        _ = [event async for event in provider.stream(request)]

    assert captured.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert captured.value.retryable is False
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_rejects_invalid_tool_json_without_completed_event() -> None:
    wire_events = [
        (name, data)
        for name, data in anthropic_stream_events()
        if not (name == "content_block_delta" and data["delta"].get("partial_json") == "true}")
    ]
    provider, client, _ = streaming_provider(wire_events)
    emitted: list[object] = []

    with pytest.raises(ProviderError):
        async for event in provider.stream(
            ModelRequest(
                request_id="request-1",
                system_prompt="",
                messages=(Message.user_text("inspect"),),
            )
        ):
            emitted.append(event)

    assert not any(isinstance(event, ResponseCompleted) for event in emitted)
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_normalizes_in_stream_error_without_leaking_message() -> None:
    secret = "secret-provider-detail"
    wire_events = [
        anthropic_stream_events()[0],
        (
            "error",
            {
                "type": "error",
                "error": {
                    "type": "overloaded_error",
                    "message": f"overloaded {secret}",
                },
            },
        ),
    ]
    provider, client, _ = streaming_provider(wire_events)

    with pytest.raises(ProviderError) as captured:
        _ = [
            event
            async for event in provider.stream(
                ModelRequest(
                    request_id="request-1",
                    system_prompt="",
                    messages=(Message.user_text("inspect"),),
                )
            )
        ]

    assert captured.value.code is ProviderErrorCode.SERVER
    assert captured.value.retryable is True
    assert secret not in str(captured.value)
    await client.aclose()
