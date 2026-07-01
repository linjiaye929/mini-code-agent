from __future__ import annotations

import json
from hashlib import sha256

import pytest

from mini_code_agent.agent.models import AgentResult, StopReason
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.subagents.evidence import (
    SubagentEvidenceError,
    extract_subagent_evidence,
    subagent_result_sha256,
)
from mini_code_agent.subagents.models import SubagentEvidenceItem


def agent_result_with_tools(
    calls: tuple[tuple[str, str, str, bool], ...],
) -> AgentResult:
    tool_calls = tuple(
        ToolCall(id=call_id, name=tool_name, arguments={}) for call_id, tool_name, _, _ in calls
    )
    results = tuple(
        ToolResult(tool_call_id=call_id, content=content, is_error=is_error)
        for call_id, _, content, is_error in calls
    )
    return AgentResult(
        run_id="subagent-child-1",
        messages=(
            Message.user_text("task"),
            Message(role=MessageRole.ASSISTANT, content=tool_calls),
            Message(role=MessageRole.USER, content=results),
            Message.assistant_text("done"),
        ),
        stop_reason=StopReason.COMPLETED,
        turns=2,
        tool_calls=len(calls),
        usage=TokenUsage(input_tokens=10, output_tokens=2),
        final_text="done",
    )


def test_extract_evidence_returns_only_bounded_hash_metadata() -> None:
    secret = "do-not-copy-result"
    result = agent_result_with_tools(
        (
            ("call-1", "read_file", secret, False),
            ("call-2", "search_text", "two", True),
        )
    )

    evidence = extract_subagent_evidence(result, max_items=2)

    assert [item.tool_name for item in evidence] == ["read_file", "search_text"]
    assert evidence[0].content_chars == len(secret)
    assert evidence[0].content_sha256 == sha256(secret.encode()).hexdigest()
    assert evidence[1].is_error is True
    assert secret not in json.dumps([item.model_dump() for item in evidence])


def test_extract_evidence_returns_empty_for_tool_free_result() -> None:
    result = AgentResult(
        run_id="subagent-child-1",
        messages=(Message.user_text("task"), Message.assistant_text("done")),
        stop_reason=StopReason.COMPLETED,
        turns=1,
        tool_calls=0,
        usage=TokenUsage(),
        final_text="done",
    )

    assert extract_subagent_evidence(result, max_items=0) == ()


@pytest.mark.parametrize(
    "messages",
    [
        (
            Message.user_text("task"),
            Message(
                role=MessageRole.USER,
                content=(ToolResult(tool_call_id="missing", content="result"),),
            ),
        ),
        (
            Message.user_text("task"),
            Message(
                role=MessageRole.ASSISTANT,
                content=(
                    ToolCall(id="duplicate", name="read_file", arguments={}),
                    ToolCall(id="duplicate", name="search_text", arguments={}),
                ),
            ),
        ),
        (
            Message.user_text("task"),
            Message(
                role=MessageRole.ASSISTANT,
                content=(ToolCall(id="call-1", name="read_file", arguments={}),),
            ),
        ),
        (
            Message.user_text("task"),
            Message(
                role=MessageRole.ASSISTANT,
                content=(ToolCall(id="call-1", name="read_file", arguments={}),),
            ),
            Message(
                role=MessageRole.USER,
                content=(
                    ToolResult(tool_call_id="call-1", content="one"),
                    ToolResult(tool_call_id="call-1", content="two"),
                ),
            ),
        ),
    ],
)
def test_extract_evidence_rejects_malformed_correlation(
    messages: tuple[Message, ...],
) -> None:
    result = AgentResult(
        run_id="subagent-child-1",
        messages=messages,
        stop_reason=StopReason.INVALID_RESPONSE,
        turns=1,
        tool_calls=1,
        usage=TokenUsage(),
        error="invalid",
    )

    with pytest.raises(SubagentEvidenceError):
        extract_subagent_evidence(result, max_items=4)


def test_extract_evidence_rejects_budget_overflow() -> None:
    result = agent_result_with_tools(
        (
            ("call-1", "read_file", "one", False),
            ("call-2", "search_text", "two", False),
        )
    )

    with pytest.raises(SubagentEvidenceError):
        extract_subagent_evidence(result, max_items=1)


def test_result_hash_is_canonical_and_rejects_non_finite_values() -> None:
    left = SubagentEvidenceItem(
        tool_call_id="call-1",
        tool_name="read_file",
        is_error=False,
        content_chars=3,
        content_sha256="a" * 64,
    )
    right = SubagentEvidenceItem.model_validate(
        {
            "content_sha256": "a" * 64,
            "content_chars": 3,
            "is_error": False,
            "tool_name": "read_file",
            "tool_call_id": "call-1",
        }
    )

    assert subagent_result_sha256(left) == subagent_result_sha256(right)


def test_public_evidence_error_is_static() -> None:
    error = SubagentEvidenceError()

    assert str(error) == "Subagent transcript evidence was invalid."
