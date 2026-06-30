from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mini_code_agent.agent.models import StopReason
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    RepairRunStatus,
    SessionTraceLimits,
)
from mini_code_agent.persistence.repair import (
    SqliteRepairJournal,
    get_repair_run,
    read_repair_trace,
    verify_repair_trace,
)
from mini_code_agent.persistence.schema import initialize_database
from mini_code_agent.repair.events import (
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


def lifecycle(repair_id: str = "repair-1") -> tuple[RepairEvent, ...]:
    started_at = datetime.now(UTC) + timedelta(seconds=1)
    return (
        RepairStarted(
            repair_id=repair_id,
            timestamp=started_at,
            scope_sha256=SHA,
            max_attempts=3,
            test_target_count=1,
            editable_path_count=1,
        ),
        RepairAttemptStarted(
            repair_id=repair_id,
            timestamp=started_at + timedelta(milliseconds=1),
            attempt=1,
            failure_sha256=SHA,
        ),
        RepairVerificationStarted(
            repair_id=repair_id,
            timestamp=started_at + timedelta(milliseconds=2),
            attempt=1,
            patch_sha256="b" * 64,
            patch_bytes=10,
        ),
        RepairAttemptCompleted(
            repair_id=repair_id,
            timestamp=started_at + timedelta(milliseconds=3),
            attempt=1,
            worker_run_id="worker-1",
            worker_stop_reason=StopReason.COMPLETED,
            patch_sha256="b" * 64,
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
            failure_sha256=SHA,
            elapsed_ms=50,
        ),
        RepairStopped(
            repair_id=repair_id,
            timestamp=started_at + timedelta(milliseconds=4),
            reason=RepairStopReason.REPEATED_FAILURE,
            attempts=1,
            final_status_sha256="c" * 64,
            final_diff_sha256="d" * 64,
        ),
    )


def journal(
    database: Path,
    *,
    limits: SessionTraceLimits | None = None,
    secrets: tuple[str, ...] = (),
) -> SqliteRepairJournal:
    active_limits = limits or SessionTraceLimits()
    initialize_database(database, active_limits)
    return SqliteRepairJournal(database, active_limits, secrets)


def test_repair_journal_appends_lifecycle_and_reopens_verified(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    active = journal(database)
    for event in lifecycle():
        active.append(event)

    records = read_repair_trace(
        database,
        SessionTraceLimits(),
        "repair-1",
        limit=10,
    )
    verification = verify_repair_trace(
        database,
        SessionTraceLimits(),
        "repair-1",
    )
    run = get_repair_run(database, SessionTraceLimits(), "repair-1")

    assert tuple(record.event.type for record in records) == (
        "repair_started",
        "repair_attempt_started",
        "repair_verification_started",
        "repair_attempt_completed",
        "repair_stopped",
    )
    assert tuple(record.sequence for record in records) == (1, 2, 3, 4, 5)
    assert records[0].previous_sha256 == EMPTY_TRACE_SHA256
    assert verification.event_count == 5
    assert verification.trace_head_sha256 == records[-1].event_sha256
    assert run.status is RepairRunStatus.STOPPED
    assert run.stop_reason is RepairStopReason.REPEATED_FAILURE
    assert run.event_count == 5


def test_same_event_is_idempotent_but_conflict_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    active = journal(database)
    original = lifecycle()[0]
    active.append(original)
    active.append(original)

    with pytest.raises(PersistenceError) as captured:
        active.append(
            original.model_copy(
                update={
                    "repair_id": "repair-2",
                    "scope_sha256": "b" * 64,
                }
            )
        )

    assert captured.value.code is PersistenceErrorCode.EVENT_CONFLICT
    assert get_repair_run(database, SessionTraceLimits(), "repair-1").event_count == 1


def test_repair_journal_rejects_invalid_transitions(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    active = journal(database)
    events = lifecycle()

    with pytest.raises(PersistenceError) as before_start:
        active.append(events[1])
    active.append(events[0])
    with pytest.raises(PersistenceError) as skipped_stage:
        active.append(events[2])
    active.append(events[1])
    active.append(events[2])
    with pytest.raises(PersistenceError) as wrong_attempt:
        active.append(events[3].model_copy(update={"attempt": 2, "event_id": "wrong-attempt"}))
    active.append(events[3])
    active.append(events[4])
    with pytest.raises(PersistenceError) as after_stop:
        active.append(events[1].model_copy(update={"event_id": "after-stop", "attempt": 2}))

    assert before_start.value.code is PersistenceErrorCode.INVALID_TRANSITION
    assert skipped_stage.value.code is PersistenceErrorCode.INVALID_TRANSITION
    assert wrong_attempt.value.code is PersistenceErrorCode.INVALID_TRANSITION
    assert after_stop.value.code is PersistenceErrorCode.INVALID_TRANSITION


def test_started_only_repair_is_explicitly_active_and_not_replayed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    active = journal(database)
    active.append(lifecycle()[0])

    run = get_repair_run(database, SessionTraceLimits(), "repair-1")
    records = read_repair_trace(
        database,
        SessionTraceLimits(),
        "repair-1",
    )

    assert run.status is RepairRunStatus.ACTIVE
    assert run.stopped_at is None
    assert tuple(record.event.type for record in records) == ("repair_started",)


def test_repair_payload_and_event_count_limits_are_atomic(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    limits = SessionTraceLimits(max_events_per_session=1, max_event_bytes=2_000)
    active = journal(database, limits=limits)
    events = lifecycle()
    active.append(events[0])

    with pytest.raises(PersistenceError) as count_error:
        active.append(events[1])

    assert count_error.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    assert get_repair_run(database, limits, "repair-1").event_count == 1

    small_database = tmp_path / "small.db"
    small_limits = SessionTraceLimits(max_event_bytes=256)
    small = journal(small_database, limits=small_limits)
    with pytest.raises(PersistenceError) as size_error:
        small.append(events[0])

    assert size_error.value.code is PersistenceErrorCode.LIMIT_EXCEEDED
    with pytest.raises(PersistenceError) as missing:
        get_repair_run(small_database, small_limits, "repair-1")
    assert missing.value.code is PersistenceErrorCode.RUN_NOT_FOUND


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE repair_events SET payload_json = '{}' WHERE sequence = 2",
        "UPDATE repair_events SET event_sha256 = zeroblob(64) WHERE sequence = 2",
        "UPDATE repair_events SET previous_sha256 = zeroblob(64) WHERE sequence = 2",
        "UPDATE repair_runs SET trace_head_sha256 = zeroblob(64)",
        "UPDATE repair_runs SET scope_sha256 = "
        "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
        """
        UPDATE repair_runs
        SET status = 'active', stopped_at = NULL, stop_reason = NULL
        """,
    ),
)
def test_repair_trace_verification_detects_tampering(
    tmp_path: Path,
    statement: str,
) -> None:
    database = tmp_path / "state.db"
    active = journal(database)
    for event in lifecycle():
        active.append(event)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(statement)

    with pytest.raises(PersistenceError) as captured:
        verify_repair_trace(database, SessionTraceLimits(), "repair-1")

    assert captured.value.code is PersistenceErrorCode.TRACE_CORRUPT


def test_repair_stopped_error_is_scrubbed_before_storage(tmp_path: Path) -> None:
    secret = "REPAIR_SECRET_123"
    database = tmp_path / "state.db"
    active = journal(database, secrets=(secret,))
    started, *_, stopped = lifecycle()
    active.append(started)
    active.append(
        stopped.model_copy(
            update={
                "attempts": 0,
                "error": f"failed with {secret}",
            }
        )
    )

    assert secret.encode() not in database.read_bytes()
    records = read_repair_trace(
        database,
        SessionTraceLimits(),
        "repair-1",
    )
    terminal = records[-1].event
    assert isinstance(terminal, RepairStopped)
    assert terminal.error == "failed with ***"


def test_busy_repair_database_fails_within_configured_budget(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    limits = SessionTraceLimits(busy_timeout_ms=10)
    active = journal(database, limits=limits)
    with closing(sqlite3.connect(database, isolation_level=None)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(PersistenceError) as captured:
            active.append(lifecycle()[0])
        blocker.rollback()

    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
