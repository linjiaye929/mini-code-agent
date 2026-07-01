from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import TypeAdapter, ValidationError

from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.subagents.events import (
    RecordingSubagentEventSink,
    SubagentBatchCompleted,
    SubagentBatchStarted,
    SubagentCompleted,
    SubagentEvent,
    SubagentStarted,
)
from mini_code_agent.subagents.models import SubagentStatus


def completed_event() -> SubagentCompleted:
    return SubagentCompleted(
        parent_tool_call_id="parent-1",
        profile_id="review",
        child_id="child-1",
        ordinal=0,
        status=SubagentStatus.COMPLETED,
        duration_ms=12,
        turns=2,
        tool_calls=1,
        usage=TokenUsage(input_tokens=10, output_tokens=3),
        result_sha256="a" * 64,
    )


def test_subagent_events_omit_task_prompt_summary_and_results() -> None:
    event = completed_event()

    assert set(event.model_dump()) == {
        "event_id",
        "timestamp",
        "type",
        "parent_tool_call_id",
        "profile_id",
        "child_id",
        "ordinal",
        "status",
        "duration_ms",
        "turns",
        "tool_calls",
        "usage",
        "result_sha256",
    }
    payload = event.model_dump_json()
    assert "task" not in payload
    assert "prompt" not in payload
    assert "summary" not in payload
    assert "content" not in payload


def test_event_union_round_trips_all_lifecycle_types() -> None:
    events: tuple[SubagentEvent, ...] = (
        SubagentBatchStarted(
            parent_tool_call_id="parent-1",
            profile_id="review",
            task_count=1,
        ),
        SubagentStarted(
            parent_tool_call_id="parent-1",
            profile_id="review",
            child_id="child-1",
            ordinal=0,
        ),
        completed_event(),
        SubagentBatchCompleted(
            parent_tool_call_id="parent-1",
            profile_id="review",
            duration_ms=14,
            completed=1,
            stopped=0,
            timed_out=0,
            failed=0,
            result_sha256="b" * 64,
        ),
    )
    adapter = TypeAdapter[SubagentEvent](SubagentEvent)

    assert tuple(adapter.validate_json(item.model_dump_json()) for item in events) == events


def test_recording_sink_preserves_event_order() -> None:
    sink = RecordingSubagentEventSink()
    started = SubagentStarted(
        parent_tool_call_id="parent-1",
        profile_id="review",
        child_id="child-1",
        ordinal=0,
    )
    completed = completed_event()

    sink.publish(started)
    sink.publish(completed)

    assert sink.events == [started, completed]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SubagentBatchStarted(
            parent_tool_call_id="x" * 129,
            profile_id="review",
            task_count=1,
        ),
        lambda: SubagentBatchStarted(
            parent_tool_call_id="parent-1",
            profile_id="review",
            task_count=5,
        ),
        lambda: SubagentStarted(
            parent_tool_call_id="parent-1",
            profile_id="review",
            child_id="../invalid",
            ordinal=0,
        ),
        lambda: SubagentCompleted.model_validate(
            completed_event().model_dump() | {"duration_ms": 3_700_001}
        ),
        lambda: SubagentBatchCompleted(
            parent_tool_call_id="parent-1",
            profile_id="review",
            duration_ms=1,
            completed=5,
            stopped=0,
            timed_out=0,
            failed=0,
            result_sha256="b" * 64,
        ),
    ],
)
def test_events_reject_unbounded_or_invalid_metadata(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValidationError):
        factory()
