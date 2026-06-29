from collections.abc import Mapping
from typing import cast

import pytest
from pydantic import ValidationError

from mini_code_agent.domain.content import TextBlock, ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole


def test_user_and_assistant_text_helpers_create_immutable_messages() -> None:
    user = Message.user_text("inspect the project")
    assistant = Message.assistant_text("ready")

    assert user.role is MessageRole.USER
    assert user.content == (TextBlock(text="inspect the project"),)
    assert assistant.role is MessageRole.ASSISTANT
    assert assistant.content == (TextBlock(text="ready"),)

    with pytest.raises(ValidationError):
        user.__setattr__("role", MessageRole.ASSISTANT)


def test_assistant_message_can_request_a_native_tool_call() -> None:
    call = ToolCall(id="call-1", name="runtime_info", arguments={})

    message = Message(role=MessageRole.ASSISTANT, content=(call,))

    assert message.tool_calls == (call,)


def test_user_message_can_carry_a_correlated_tool_result() -> None:
    result = ToolResult(
        tool_call_id="call-1",
        content='{"python_version":"3.13.14"}',
    )

    message = Message(role=MessageRole.USER, content=(result,))

    assert message.tool_results == (result,)


def test_role_invariants_reject_tool_calls_from_user() -> None:
    with pytest.raises(ValidationError, match="user message cannot contain ToolCall"):
        Message(
            role=MessageRole.USER,
            content=(ToolCall(id="call-1", name="runtime_info", arguments={}),),
        )


def test_role_invariants_reject_tool_results_from_assistant() -> None:
    with pytest.raises(ValidationError, match="assistant message cannot contain ToolResult"):
        Message(
            role=MessageRole.ASSISTANT,
            content=(ToolResult(tool_call_id="call-1", content="ok"),),
        )


def test_tool_call_arguments_are_recursively_immutable() -> None:
    call = ToolCall(
        id="call-1",
        name="runtime_info",
        arguments={"nested": {"items": ["one"]}},
    )

    with pytest.raises(TypeError):
        cast(dict[str, object], call.arguments)["new"] = "value"

    nested = call.arguments["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["new"] = "value"

    items = nested["items"]
    assert isinstance(items, tuple)
    assert call.model_dump()["arguments"] == {"nested": {"items": ["one"]}}
    assert ToolCall.model_validate_json(call.model_dump_json()) == call
