from collections.abc import Callable

import pytest
from pydantic import TypeAdapter, ValidationError

from mini_code_agent.agent import events as event_models
from mini_code_agent.agent.events import (
    AgentEvent,
    CheckpointSaved,
    ContextCompacted,
    RecordingEventSink,
    RunStarted,
    RunStopped,
)
from mini_code_agent.agent.models import AgentLimits, AgentResult, StopReason
from mini_code_agent.domain.messages import Message
from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.tools.base import SideEffect


def test_recording_sink_preserves_typed_event_order() -> None:
    sink = RecordingEventSink()
    event = RunStarted(run_id="run-1", max_turns=4)

    sink.publish(event)

    assert sink.events == [event]
    assert sink.events[0].run_id == "run-1"


def test_lifecycle_events_have_unique_immutable_event_ids() -> None:
    model_started = event_models.ModelStarted(
        run_id="run-1",
        turn=1,
        request_id="run-1:1",
    )
    tool_started = event_models.ToolStarted(
        run_id="run-1",
        turn=1,
        tool_call_id="call-1",
        tool_name="write_file",
        side_effect=SideEffect.WRITE,
    )
    stopped = RunStopped(
        run_id="run-1",
        turns=1,
        reason=StopReason.COMPLETED,
        tool_calls=1,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )

    event_ids = {model_started.event_id, tool_started.event_id, stopped.event_id}
    assert len(event_ids) == 3
    assert all(len(event_id) == 36 for event_id in event_ids)
    with pytest.raises(ValidationError):
        stopped.event_id = "changed"  # type: ignore[misc]


def test_started_events_round_trip_through_agent_event_union() -> None:
    adapter = TypeAdapter[AgentEvent](AgentEvent)
    events = (
        event_models.ModelStarted(run_id="run-1", turn=1, request_id="run-1:1"),
        event_models.ToolStarted(
            run_id="run-1",
            turn=1,
            tool_call_id="call-1",
            tool_name="run_command",
            side_effect=SideEffect.EXECUTE,
        ),
    )

    reparsed = tuple(adapter.validate_json(event.model_dump_json()) for event in events)

    assert reparsed == events


def test_run_stopped_serializes_cumulative_metrics() -> None:
    event = RunStopped(
        run_id="run-1",
        turns=3,
        reason=StopReason.PROVIDER_ERROR,
        tool_calls=2,
        usage=TokenUsage(input_tokens=100, output_tokens=25),
        error="Provider request failed.",
    )

    assert event.model_dump(mode="json") == {
        "event_id": event.event_id,
        "run_id": "run-1",
        "timestamp": event.timestamp.isoformat().replace("+00:00", "Z"),
        "type": "run_stopped",
        "turns": 3,
        "reason": "provider_error",
        "tool_calls": 2,
        "usage": {"input_tokens": 100, "output_tokens": 25},
        "error": "Provider request failed.",
    }


@pytest.mark.parametrize(
    "event",
    [
        lambda: RunStarted(run_id="x" * 97, max_turns=1),
        lambda: event_models.ModelStarted(run_id="run-1", turn=101, request_id="request"),
        lambda: event_models.ModelStarted(run_id="run-1", turn=1, request_id="x" * 194),
        lambda: event_models.ToolStarted(
            run_id="run-1",
            turn=1,
            tool_call_id="x" * 129,
            tool_name="read_file",
            side_effect=SideEffect.READ_ONLY,
        ),
        lambda: RunStopped(
            run_id="run-1",
            turns=101,
            reason=StopReason.MAX_TURNS,
        ),
        lambda: RunStopped(
            run_id="run-1",
            turns=1,
            reason=StopReason.PROVIDER_ERROR,
            error="x" * 501,
        ),
    ],
)
def test_lifecycle_events_reject_unbounded_metadata(
    event: Callable[[], object],
) -> None:
    with pytest.raises(ValidationError):
        event()


def test_agent_limits_reject_unbounded_zero_turn_configuration() -> None:
    with pytest.raises(ValidationError):
        AgentLimits(max_turns=0)


def test_agent_result_success_depends_on_stop_reason() -> None:
    result = AgentResult(
        run_id="run-1",
        messages=(Message.user_text("work"), Message.assistant_text("done")),
        stop_reason=StopReason.COMPLETED,
        turns=1,
        tool_calls=0,
        usage=TokenUsage(),
        final_text="done",
    )

    assert result.succeeded is True


def test_context_compacted_is_typed_bounded_and_recordable() -> None:
    sink = RecordingEventSink()
    event = ContextCompacted(
        run_id="run-1",
        turn=3,
        estimated_before=10_000,
        estimated_after=8_000,
        omitted_messages=4,
        omitted_tool_exchanges=2,
        transcript_sha256="a" * 64,
    )

    sink.publish(event)

    assert sink.events == [event]
    assert event.type == "context_compacted"
    assert "omitted content" not in event.model_dump_json()
    with pytest.raises(ValidationError):
        event.omitted_messages = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "values",
    [
        {"turn": 0},
        {"estimated_before": -1},
        {"estimated_before": 10, "estimated_after": 11},
        {"omitted_messages": 0},
        {"omitted_messages": 1, "omitted_tool_exchanges": 1},
        {"transcript_sha256": "invalid"},
    ],
)
def test_context_compacted_rejects_inconsistent_metadata(
    values: dict[str, object],
) -> None:
    complete: dict[str, object] = {
        "run_id": "run-1",
        "turn": 1,
        "estimated_before": 10,
        "estimated_after": 5,
        "omitted_messages": 2,
        "omitted_tool_exchanges": 1,
        "transcript_sha256": "0" * 64,
    }
    complete.update(values)

    with pytest.raises(ValidationError):
        ContextCompacted.model_validate(complete)


def test_checkpoint_saved_event_is_bounded() -> None:
    event = CheckpointSaved(
        run_id="run-1",
        checkpoint_id="checkpoint-1",
        turn=1,
        message_count=3,
        transcript_sha256="a" * 64,
    )

    assert event.type == "checkpoint_saved"
    with pytest.raises(ValidationError):
        CheckpointSaved(
            run_id="run-1",
            checkpoint_id="../invalid",
            turn=1,
            message_count=3,
            transcript_sha256="a" * 64,
        )
