from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from mini_code_agent.domain.content import TextBlock, ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
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
from mini_code_agent.providers.openai_compatible import OpenAICompatibleProvider
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
                    ToolResult(tool_call_id="call-1", content="{}"),
                ),
            ),
        ),
        tools=(runtime_tool(),),
    )


def openai_response(
    *,
    content: str | None = "done",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "created": 1,
        "model": "compatible-test",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        },
    }


def provider_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> tuple[OpenAICompatibleProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        api_key=SecretStr("test-key"),
        model="compatible-test",
        base_url="https://provider.test/v1",
        extra_headers=extra_headers,
        client=client,
    )
    return provider, client


@pytest.mark.asyncio
async def test_complete_converts_domain_request_to_chat_completions() -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://provider.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["x-tenant"] == "tenant-1"
        captured_body.update(cast(dict[str, Any], json.loads(request.content)))
        return httpx.Response(
            200,
            json=openai_response(),
            headers={"x-request-id": "req_1"},
            request=request,
        )

    provider, client = provider_with_handler(handler, extra_headers={"X-Tenant": "tenant-1"})

    result = await provider.complete(tool_round_trip_request())

    assert captured_body == {
        "model": "compatible-test",
        "messages": [
            {"role": "system", "content": "Work carefully."},
            {"role": "user", "content": "Inspect."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "runtime_info",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
            {"role": "user", "content": "Continue after the result."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "runtime_info",
                    "description": "Return runtime metadata.",
                    "parameters": {
                        "type": "object",
                        "properties": {"verbose": {"type": "boolean"}},
                    },
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
        return httpx.Response(200, json=openai_response(), request=request)

    provider, client = provider_with_handler(handler)

    await provider.complete(
        ModelRequest(
            request_id="request-1",
            system_prompt="",
            messages=(Message.user_text("hello"),),
        )
    )

    assert captured_body["messages"] == [{"role": "user", "content": "hello"}]
    assert "tools" not in captured_body
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_preserves_tool_error_semantics_in_tool_content() -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(cast(dict[str, Any], json.loads(request.content)))
        return httpx.Response(200, json=openai_response(), request=request)

    provider, client = provider_with_handler(handler)
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(
            Message(
                role=MessageRole.USER,
                content=(
                    ToolResult(
                        tool_call_id="call-1",
                        content="execution failed",
                        is_error=True,
                    ),
                ),
            ),
        ),
    )

    await provider.complete(request)

    tool_message = captured_body["messages"][0]
    assert tool_message == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"content":"execution failed","is_error":true}',
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_normalizes_text_and_parallel_tool_calls() -> None:
    wire_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "runtime_info",
                "arguments": '{"verbose":true}',
            },
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {
                "name": "runtime_info",
                "arguments": '{"verbose":false}',
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=openai_response(
                content="Checking.",
                tool_calls=wire_calls,
                finish_reason="tool_calls",
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
            id="call_1",
            name="runtime_info",
            arguments={"verbose": True},
        ),
        ToolCall(
            id="call_2",
            name="runtime_info",
            arguments={"verbose": False},
        ),
    )
    assert provider.capabilities.parallel_tool_calls is True
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("stop", FinishReason.STOP),
        ("length", FinishReason.MAX_TOKENS),
        ("content_filter", FinishReason.CONTENT_FILTER),
    ],
)
async def test_complete_maps_supported_finish_reasons(
    finish_reason: str,
    expected: FinishReason,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=openai_response(finish_reason=finish_reason),
            request=request,
        )

    provider, client = provider_with_handler(handler)
    result = await provider.complete(
        ModelRequest(
            request_id="request-1",
            system_prompt="",
            messages=(Message.user_text("hello"),),
        )
    )

    assert result.finish_reason is expected
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_tolerates_missing_usage_for_compatible_server() -> None:
    response = openai_response()
    del response["usage"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response, request=request)

    provider, client = provider_with_handler(handler)
    result = await provider.complete(
        ModelRequest(
            request_id="request-1",
            system_prompt="",
            messages=(Message.user_text("hello"),),
        )
    )

    assert result.usage == TokenUsage()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_body",
    cast(
        list[dict[str, Any]],
        [
            {**openai_response(), "choices": []},
            {
                **openai_response(),
                "choices": [
                    *openai_response()["choices"],
                    *openai_response()["choices"],
                ],
            },
            {
                **openai_response(),
                "choices": [
                    {
                        "index": 1,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
            },
            openai_response(content=None),
            openai_response(
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "runtime_info",
                            "arguments": "[]",
                        },
                    }
                ],
                finish_reason="tool_calls",
            ),
            openai_response(
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "runtime_info",
                            "arguments": "{}",
                        },
                    },
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "runtime_info",
                            "arguments": "{}",
                        },
                    },
                ],
                finish_reason="tool_calls",
            ),
            openai_response(
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "runtime_info",
                            "arguments": "{}",
                        },
                    }
                ],
                finish_reason="stop",
            ),
            {
                **openai_response(),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "done",
                            "function_call": {
                                "name": "legacy",
                                "arguments": "{}",
                            },
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
            openai_response(finish_reason="unknown"),
            {"error": {"message": "failed", "type": "server_error"}},
        ],
    ),
)
async def test_complete_rejects_malformed_or_lossy_response(
    response_body: dict[str, Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body, request=request)

    provider, client = provider_with_handler(handler)

    with pytest.raises(ProviderError) as captured:
        await provider.complete(
            ModelRequest(
                request_id="request-1",
                system_prompt="",
                messages=(Message.user_text("hello"),),
            )
        )

    assert captured.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert captured.value.retryable is False
    await client.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": ""},
        {"model": "x" * 257},
        {"model": "bad model"},
        {"extra_headers": {"Authorization": "other"}},
        {"extra_headers": {"Content-Type": "text/plain"}},
        {"extra_headers": {"X-Test": "bad\nvalue"}},
        {"extra_headers": {1: "value"}},
        {"extra_headers": {"X-Test": 1}},
    ],
)
def test_provider_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    arguments: dict[str, object] = {
        "api_key": SecretStr("test-key"),
        "model": "compatible-test",
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError):
        OpenAICompatibleProvider(**arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provider_close_does_not_close_borrowed_client() -> None:
    provider, client = provider_with_handler(
        lambda request: httpx.Response(200, json=openai_response(), request=request)
    )

    await provider.aclose()

    assert client.is_closed is False
    await client.aclose()


def encode_openai_sse(events: list[dict[str, Any] | str]) -> bytes:
    lines: list[str] = []
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event, separators=(",", ":"))
        lines.append(f"data: {data}\n\n")
    return "".join(lines).encode()


def openai_stream_events(*, include_usage: bool = True) -> list[dict[str, Any] | str]:
    events: list[dict[str, Any] | str] = [
        {
            "id": "chatcmpl_1",
            "object": "chat.completion.chunk",
            "model": "compatible-test",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
            "usage": None,
        },
        {
            "id": "chatcmpl_1",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Checking"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "runtime_info",
                                    "arguments": '{"verbose":',
                                },
                            },
                            {
                                "index": 1,
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "runtime_info",
                                    "arguments": '{"verbose":',
                                },
                            },
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "function": {"arguments": "false}"},
                            },
                            {
                                "index": 0,
                                "function": {"arguments": "true}"},
                            },
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "tool_calls",
                }
            ],
        },
    ]
    if include_usage:
        events.append(
            {
                "id": "chatcmpl_1",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 7,
                    "total_tokens": 17,
                },
            }
        )
    events.append("[DONE]")
    return events


def streaming_provider(
    events: list[dict[str, Any] | str],
) -> tuple[OpenAICompatibleProvider, httpx.AsyncClient, dict[str, Any]]:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(cast(dict[str, Any], json.loads(request.content)))
        return httpx.Response(
            200,
            content=encode_openai_sse(events),
            headers={
                "content-type": "text/event-stream",
                "x-request-id": "req_stream_1",
            },
            request=request,
        )

    provider, client = provider_with_handler(handler)
    return provider, client, captured_body


@pytest.mark.asyncio
async def test_stream_normalizes_sparse_parallel_tool_chunks_and_usage() -> None:
    provider, client, captured_body = streaming_provider(openai_stream_events())
    request = ModelRequest(
        request_id="request-1",
        system_prompt="Work carefully.",
        messages=(Message.user_text("inspect"),),
        tools=(runtime_tool(),),
    )

    events = [event async for event in provider.stream(request)]

    assert captured_body["stream"] is True
    assert captured_body["stream_options"] == {"include_usage": True}
    assert events[:5] == [
        TextDelta(text="Checking"),
        ToolCallDelta(
            index=0,
            tool_call_id="call_1",
            name="runtime_info",
            partial_json='{"verbose":',
        ),
        ToolCallDelta(
            index=1,
            tool_call_id="call_2",
            name="runtime_info",
            partial_json='{"verbose":',
        ),
        ToolCallDelta(
            index=1,
            tool_call_id="call_2",
            name="runtime_info",
            partial_json="false}",
        ),
        ToolCallDelta(
            index=0,
            tool_call_id="call_1",
            name="runtime_info",
            partial_json="true}",
        ),
    ]
    completed = cast(ResponseCompleted, events[5])
    assert completed.response.finish_reason is FinishReason.TOOL_CALL
    assert completed.response.message.text == "Checking"
    assert completed.response.message.tool_calls == (
        ToolCall(
            id="call_1",
            name="runtime_info",
            arguments={"verbose": True},
        ),
        ToolCall(
            id="call_2",
            name="runtime_info",
            arguments={"verbose": False},
        ),
    )
    assert completed.response.usage == TokenUsage(input_tokens=10, output_tokens=7)
    assert completed.response.provider_request_id == "req_stream_1"
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_allows_missing_usage_from_compatible_server() -> None:
    provider, client, _ = streaming_provider(openai_stream_events(include_usage=False))

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

    completed = cast(ResponseCompleted, events[-1])
    assert completed.response.usage == TokenUsage()
    await client.aclose()


def copy_openai_events(
    events: list[dict[str, Any] | str],
) -> list[dict[str, Any] | str]:
    return cast(list[dict[str, Any] | str], json.loads(json.dumps(events)))


def mutate_openai_tool_chunk(
    mutation: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any] | str]:
    events = copy_openai_events(openai_stream_events())
    for event in events:
        if not isinstance(event, dict):
            continue
        choices = event.get("choices")
        if not choices:
            continue
        tool_calls = choices[0].get("delta", {}).get("tool_calls")
        if tool_calls:
            mutation(tool_calls[0])
            return events
    raise AssertionError("tool chunk not found")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire_events",
    cast(
        list[list[dict[str, Any] | str]],
        [
            openai_stream_events()[:-1],
            ["[DONE]"],
            mutate_openai_tool_chunk(lambda call: call.update({"index": 2})),
            mutate_openai_tool_chunk(lambda call: call.update({"id": ""})),
            mutate_openai_tool_chunk(lambda call: call["function"].update({"name": "Bad Name"})),
            [
                *openai_stream_events()[:-2],
                {
                    "id": "chatcmpl_1",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "unknown",
                        }
                    ],
                },
                "[DONE]",
            ],
        ],
    ),
)
async def test_stream_rejects_invalid_lifecycle_or_metadata(
    wire_events: list[dict[str, Any] | str],
) -> None:
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

    assert captured.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert captured.value.retryable is False
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_rejects_invalid_tool_json_without_completed_event() -> None:
    events = copy_openai_events(openai_stream_events())
    for event in events:
        if not isinstance(event, dict):
            continue
        choices = event.get("choices")
        if choices and choices[0].get("delta", {}).get("tool_calls"):
            tool_calls = choices[0]["delta"]["tool_calls"]
            for call in tool_calls:
                call["function"]["arguments"] = '{"broken":'
    provider, client, _ = streaming_provider(events)
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
async def test_stream_normalizes_error_event_without_leaking_raw_message() -> None:
    secret = "provider-secret-detail"
    provider, client, _ = streaming_provider(
        [
            {
                "error": {
                    "type": "rate_limit_error",
                    "message": f"limited {secret}",
                }
            },
            "[DONE]",
        ]
    )

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

    assert captured.value.code is ProviderErrorCode.RATE_LIMIT
    assert captured.value.retryable is True
    assert secret not in str(captured.value)
    await client.aclose()
