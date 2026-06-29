import pytest
from pydantic import ValidationError

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
    ResponseCompleted,
    TextDelta,
    TokenUsage,
)
from mini_code_agent.providers.fake import ScriptedProvider


def response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=4, output_tokens=2),
        provider_request_id="provider-1",
    )


@pytest.mark.asyncio
async def test_scripted_provider_records_requests_and_returns_response() -> None:
    provider = ScriptedProvider([response("done")])
    request = ModelRequest(
        request_id="request-1",
        system_prompt="Be precise.",
        messages=(Message.user_text("work"),),
    )

    result = await provider.complete(request)

    assert result.message.text == "done"
    assert provider.requests == [request]
    assert provider.capabilities.tool_calling is True


@pytest.mark.asyncio
async def test_scripted_provider_exhaustion_is_normalized() -> None:
    provider = ScriptedProvider([])
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("work"),),
    )

    with pytest.raises(ProviderError) as captured:
        await provider.complete(request)

    assert captured.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_scripted_provider_can_raise_a_normalized_error() -> None:
    provider = ScriptedProvider(
        [
            ProviderError(
                ProviderErrorCode.RATE_LIMIT,
                "Provider is temporarily rate limited.",
                retryable=True,
            )
        ]
    )
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("work"),),
    )

    with pytest.raises(ProviderError) as captured:
        await provider.complete(request)

    assert captured.value.code is ProviderErrorCode.RATE_LIMIT
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_stream_emits_text_and_completed_response() -> None:
    provider = ScriptedProvider([response("done")])
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("work"),),
    )

    events = [event async for event in provider.stream(request)]

    assert events[0] == TextDelta(text="done")
    assert events[1] == ResponseCompleted(response=response("done"))


def test_response_rejects_tool_call_with_stop_reason() -> None:
    with pytest.raises(ValidationError, match="ToolCall requires tool_call finish reason"):
        ModelResponse(
            message=Message(
                role=MessageRole.ASSISTANT,
                content=(
                    ToolCall(id="call-1", name="runtime_info", arguments={}),
                ),
            ),
            finish_reason=FinishReason.STOP,
        )
