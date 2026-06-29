from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mini_code_agent.agent.events import RunStarted
from mini_code_agent.persistence.models import (
    RunRecord,
    RunStatus,
    SessionRecord,
    SessionStatus,
    SessionTraceLimits,
    TraceRecord,
    TraceVerification,
)


def test_session_trace_limits_have_bounded_defaults() -> None:
    limits = SessionTraceLimits()

    assert limits.max_event_bytes == 65_536
    assert limits.max_events_per_session == 100_000
    assert limits.max_query_rows == 1_000
    assert limits.busy_timeout_ms == 250
    assert SessionTraceLimits(max_event_bytes=256).max_event_bytes == 256


@pytest.mark.parametrize(
    "values",
    [
        {"max_event_bytes": 255},
        {"max_event_bytes": 1_048_577},
        {"max_events_per_session": 0},
        {"max_events_per_session": 1_000_001},
        {"max_query_rows": 0},
        {"max_query_rows": 10_001},
        {"busy_timeout_ms": 0},
        {"busy_timeout_ms": 5_001},
    ],
)
def test_session_trace_limits_reject_out_of_range_values(
    values: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        SessionTraceLimits(**values)


def test_session_and_run_status_values_are_stable() -> None:
    assert {status.value for status in SessionStatus} == {
        "ready",
        "active",
        "completed",
        "stopped",
    }
    assert {status.value for status in RunStatus} == {
        "active",
        "completed",
        "stopped",
    }


def test_session_record_is_immutable_and_counters_are_consistent() -> None:
    now = datetime.now(UTC)
    session = SessionRecord(
        session_id="session-1",
        created_at=now,
        updated_at=now,
        status=SessionStatus.READY,
        event_count=2,
        next_sequence=3,
        trace_head_sha256="a" * 64,
    )

    assert session.schema_version == 1
    with pytest.raises(ValidationError):
        session.status = SessionStatus.ACTIVE
    with pytest.raises(ValidationError):
        SessionRecord(
            session_id="session-1",
            created_at=now,
            updated_at=now,
            status=SessionStatus.READY,
            event_count=2,
            next_sequence=4,
            trace_head_sha256="a" * 64,
        )


def test_run_record_requires_consistent_terminal_metadata() -> None:
    now = datetime.now(UTC)

    active = RunRecord(
        run_id="run-1",
        session_id="session-1",
        started_at=now,
        status=RunStatus.ACTIVE,
    )

    assert active.stopped_at is None
    assert active.stop_reason is None
    with pytest.raises(ValidationError):
        RunRecord(
            run_id="run-1",
            session_id="session-1",
            started_at=now,
            status=RunStatus.COMPLETED,
        )


def test_trace_record_and_verification_are_typed_and_bounded() -> None:
    event = RunStarted(run_id="run-1", max_turns=8)
    trace = TraceRecord(
        sequence=1,
        session_id="session-1",
        event=event,
        previous_sha256="0" * 64,
        event_sha256="a" * 64,
    )
    verification = TraceVerification(
        session_id="session-1",
        event_count=1,
        trace_head_sha256="a" * 64,
    )

    assert trace.run_id == "run-1"
    assert trace.event_id == event.event_id
    assert verification.event_count == 1
    with pytest.raises(ValidationError):
        TraceRecord(
            sequence=0,
            session_id="session-1",
            event=event,
            previous_sha256="0" * 64,
            event_sha256="a" * 64,
        )
