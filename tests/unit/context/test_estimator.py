from __future__ import annotations

import pytest
from pydantic import JsonValue, ValidationError

from mini_code_agent.context.estimator import Utf8TokenEstimator
from mini_code_agent.context.models import ContextLimits
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.tools.base import SideEffect, ToolDefinition


def definition(
    *,
    properties: dict[str, JsonValue] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name="test_tool",
        description="Test tool.",
        input_schema={
            "type": "object",
            "properties": properties or {},
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )


def estimate(
    *,
    system_prompt: str = "",
    messages: tuple[Message, ...] = (Message.user_text("hello"),),
    tools: tuple[ToolDefinition, ...] = (),
) -> int:
    return Utf8TokenEstimator().estimate(
        system_prompt=system_prompt,
        messages=messages,
        tools=tools,
    )


def test_context_limits_are_bounded_and_leave_request_budget() -> None:
    limits = ContextLimits()

    assert limits.max_context_tokens == 32_768
    assert limits.reserved_output_tokens == 4_096
    assert limits.usable_input_tokens == 28_672
    assert limits.marker_max_chars == 500


@pytest.mark.parametrize(
    "values",
    [
        {"max_context_tokens": 255},
        {"max_context_tokens": 1_000_001},
        {"reserved_output_tokens": 0},
        {"marker_max_chars": 127},
        {"max_context_tokens": 256, "reserved_output_tokens": 256},
    ],
)
def test_context_limits_reject_invalid_values(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        ContextLimits.model_validate(values)


def test_estimator_is_deterministic_for_mapping_insertion_order() -> None:
    first = Message(
        role=MessageRole.ASSISTANT,
        content=(
            ToolCall(
                id="call-1",
                name="test_tool",
                arguments={"alpha": 1, "beta": 2},
            ),
        ),
    )
    second = Message(
        role=MessageRole.ASSISTANT,
        content=(
            ToolCall(
                id="call-1",
                name="test_tool",
                arguments={"beta": 2, "alpha": 1},
            ),
        ),
    )

    assert estimate(messages=(first,)) == estimate(messages=(second,))


def test_estimator_accounts_for_utf8_bytes_and_text_growth() -> None:
    ascii_estimate = estimate(messages=(Message.user_text("a"),))
    unicode_estimate = estimate(messages=(Message.user_text("中"),))
    longer_estimate = estimate(messages=(Message.user_text("a" * 100),))

    assert unicode_estimate > ascii_estimate
    assert longer_estimate > ascii_estimate


def test_estimator_accounts_for_system_prompt_and_tool_schema() -> None:
    baseline = estimate()
    with_system = estimate(system_prompt="Follow the system policy.")
    with_small_tool = estimate(tools=(definition(),))
    with_large_tool = estimate(
        tools=(
            definition(
                properties={
                    "path": {
                        "type": "string",
                        "description": "x" * 500,
                    }
                }
            ),
        )
    )

    assert with_system > baseline
    assert with_small_tool > baseline
    assert with_large_tool > with_small_tool


def test_estimator_repeated_calls_return_same_positive_value() -> None:
    estimator = Utf8TokenEstimator()
    messages = (Message.user_text("goal"),)
    tools = (definition(),)

    first = estimator.estimate(
        system_prompt="system",
        messages=messages,
        tools=tools,
    )
    second = estimator.estimate(
        system_prompt="system",
        messages=messages,
        tools=tools,
    )

    assert first == second
    assert first > 0
