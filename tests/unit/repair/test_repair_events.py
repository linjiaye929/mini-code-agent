from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from mini_code_agent.agent.models import StopReason
from mini_code_agent.repair.events import (
    RecordingRepairJournal,
    RepairAttemptCompleted,
    RepairAttemptStarted,
    RepairEvent,
    RepairStarted,
    RepairStopped,
    RepairVerificationStarted,
)
from mini_code_agent.repair.models import RepairStopReason
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestExecutionStatus,
    PytestReportStatus,
)

SHA = "a" * 64
NOW = datetime(2026, 6, 30, tzinfo=UTC)


def test_repair_event_union_round_trips_every_type() -> None:
    adapter = TypeAdapter[RepairEvent](RepairEvent)
    events: tuple[RepairEvent, ...] = (
        started(),
        RepairAttemptStarted(
            repair_id="repair-1",
            timestamp=NOW,
            attempt=1,
            failure_sha256=SHA,
        ),
        RepairVerificationStarted(
            repair_id="repair-1",
            timestamp=NOW,
            attempt=1,
            patch_sha256="b" * 64,
            patch_bytes=10,
        ),
        completed(),
        stopped(),
    )

    decoded = tuple(adapter.validate_json(event.model_dump_json()) for event in events)

    assert decoded == events
    assert tuple(event.type for event in decoded) == (
        "repair_started",
        "repair_attempt_started",
        "repair_verification_started",
        "repair_attempt_completed",
        "repair_stopped",
    )


def test_events_reject_naive_time_invalid_hash_and_oversized_error() -> None:
    with pytest.raises(ValidationError):
        RepairAttemptStarted(
            repair_id="repair-1",
            timestamp=datetime(2026, 6, 30),
            attempt=1,
            failure_sha256=SHA,
        )
    with pytest.raises(ValidationError):
        RepairAttemptStarted(
            repair_id="repair-1",
            timestamp=NOW,
            attempt=1,
            failure_sha256="invalid",
        )
    with pytest.raises(ValidationError):
        RepairStopped(
            repair_id="repair-1",
            timestamp=NOW,
            reason=RepairStopReason.WORKER_FAILED,
            attempts=1,
            final_status_sha256=SHA,
            final_diff_sha256=SHA,
            error="x" * 501,
        )


def test_attempt_completion_requires_failure_hash_for_failed_test() -> None:
    with pytest.raises(ValidationError, match="failure fingerprint is inconsistent"):
        RepairAttemptCompleted(
            repair_id="repair-1",
            timestamp=NOW,
            attempt=1,
            worker_run_id="worker-1",
            worker_stop_reason=StopReason.COMPLETED,
            patch_sha256=SHA,
            patch_bytes=10,
            test_status=PytestExecutionStatus.FAILED,
            report_status=PytestReportStatus.COMPLETE,
            counts=PytestCounts(
                total=1,
                passed=0,
                failed=1,
                errors=0,
                skipped=0,
            ),
            elapsed_ms=50,
        )


def test_recording_journal_is_idempotent_and_rejects_conflict() -> None:
    journal = RecordingRepairJournal()
    event = started()

    journal.append(event)
    journal.append(event)

    assert journal.events == [event]
    conflict = event.model_copy(
        update={"scope_sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="event identifier conflicts"):
        journal.append(conflict)


def started() -> RepairStarted:
    return RepairStarted(
        event_id="event-start",
        repair_id="repair-1",
        timestamp=NOW,
        scope_sha256=SHA,
        max_attempts=3,
        test_target_count=1,
        editable_path_count=1,
    )


def completed() -> RepairAttemptCompleted:
    return RepairAttemptCompleted(
        repair_id="repair-1",
        timestamp=NOW,
        attempt=1,
        worker_run_id="worker-1",
        worker_stop_reason=StopReason.COMPLETED,
        patch_sha256="b" * 64,
        patch_bytes=10,
        test_status=PytestExecutionStatus.FAILED,
        report_status=PytestReportStatus.COMPLETE,
        counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
        failure_sha256=SHA,
        elapsed_ms=50,
    )


def stopped() -> RepairStopped:
    return RepairStopped(
        repair_id="repair-1",
        timestamp=NOW,
        reason=RepairStopReason.REPEATED_FAILURE,
        attempts=1,
        final_status_sha256="c" * 64,
        final_diff_sha256="d" * 64,
    )
