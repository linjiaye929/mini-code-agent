import pytest
from pydantic import ValidationError

from mini_code_agent.agent.events import ContextCompacted, RecordingEventSink, RunStarted
from mini_code_agent.agent.models import AgentLimits, AgentResult, StopReason
from mini_code_agent.domain.messages import Message
from mini_code_agent.providers.base import TokenUsage


def test_recording_sink_preserves_typed_event_order() -> None:
    sink = RecordingEventSink()
    event = RunStarted(run_id="run-1", max_turns=4)

    sink.publish(event)

    assert sink.events == [event]
    assert sink.events[0].run_id == "run-1"


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
