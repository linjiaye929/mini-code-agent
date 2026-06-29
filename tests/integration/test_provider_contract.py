from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.messages import Message
from mini_code_agent.providers.anthropic import AnthropicProvider
from mini_code_agent.providers.base import (
    ModelProvider,
    ModelRequest,
    ProviderError,
    ProviderErrorCode,
)
from mini_code_agent.providers.openai_compatible import OpenAICompatibleProvider
from mini_code_agent.tools.runtime_info import RuntimeInfoTool

type ProviderFactory = Callable[
    [Callable[[httpx.Request], httpx.Response]],
    tuple[ModelProvider, httpx.AsyncClient],
]


def anthropic_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[ModelProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        AnthropicProvider(
            api_key=SecretStr("anthropic-test-key"),
            model="claude-test",
            base_url="https://anthropic.test",
            client=client,
        ),
        client,
    )


def openai_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[ModelProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        OpenAICompatibleProvider(
            api_key=SecretStr("openai-test-key"),
            model="compatible-test",
            base_url="https://openai.test/v1",
            client=client,
        ),
        client,
    )


PROVIDER_FACTORIES: tuple[tuple[str, ProviderFactory], ...] = (
    ("anthropic", anthropic_factory),
    ("openai", openai_factory),
)


def anthropic_tool_response() -> dict[str, Any]:
    return {
        "id": "msg_tool",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "runtime_info",
                "input": {},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }


def anthropic_final_response() -> dict[str, Any]:
    return {
        "id": "msg_final",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "Runtime inspected."}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 8, "output_tokens": 3},
    }


def openai_tool_response() -> dict[str, Any]:
    return {
        "id": "chatcmpl_tool",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
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
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 7,
        },
    }


def openai_final_response() -> dict[str, Any]:
    return {
        "id": "chatcmpl_final",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Runtime inspected.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 3,
            "total_tokens": 11,
        },
    }


def response_sequence(provider_name: str) -> deque[dict[str, Any]]:
    if provider_name == "anthropic":
        return deque([anthropic_tool_response(), anthropic_final_response()])
    return deque([openai_tool_response(), openai_final_response()])


def assert_result_request(provider_name: str, request: httpx.Request) -> None:
    body = cast(dict[str, Any], json.loads(request.content))
    messages = cast(list[dict[str, Any]], body["messages"])
    if provider_name == "anthropic":
        result_blocks = messages[-1]["content"]
        assert result_blocks[0]["type"] == "tool_result"
        assert result_blocks[0]["tool_use_id"] == "call-1"
        assert "package_version" in result_blocks[0]["content"]
        return
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call-1"
    assert "package_version" in messages[-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider_name", "provider_factory"), PROVIDER_FACTORIES)
async def test_real_adapter_drives_unmodified_agent_tool_round_trip(
    provider_name: str,
    provider_factory: ProviderFactory,
) -> None:
    responses = response_sequence(provider_name)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 2:
            assert_result_request(provider_name, request)
        return httpx.Response(
            200,
            json=responses.popleft(),
            headers={"x-request-id": f"provider-{request_count}"},
            request=request,
        )

    provider, client = provider_factory(handler)
    runtime = AgentRuntime(provider, RuntimeInfoTool())

    result = await runtime.run(
        user_prompt="Inspect the runtime.",
        system_prompt="Use tools when needed.",
        run_id=f"{provider_name}-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_text == "Runtime inspected."
    assert result.turns == 2
    assert result.tool_calls == 1
    assert result.usage.input_tokens == 13
    assert result.usage.output_tokens == 5
    assert request_count == 2
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("_provider_name", "provider_factory"), PROVIDER_FACTORIES)
async def test_real_adapters_normalize_rate_limit_identically(
    _provider_name: str,
    provider_factory: ProviderFactory,
) -> None:
    secret = "raw-provider-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": f"limited {secret}"}},
            request=request,
        )

    provider, client = provider_factory(handler)
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("hello"),),
    )

    with pytest.raises(ProviderError) as captured:
        await provider.complete(request)

    assert captured.value.code is ProviderErrorCode.RATE_LIMIT
    assert captured.value.retryable is True
    assert secret not in str(captured.value)
    await client.aclose()
