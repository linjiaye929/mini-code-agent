from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from mini_code_agent.persistence.codec import canonical_json
from mini_code_agent.persistence.errors import (
    PersistenceError,
    PersistenceErrorCode,
)
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    IDENTIFIER_PATTERN,
    RepairRunRecord,
    RepairRunStatus,
    RepairTraceRecord,
    RepairTraceVerification,
    SessionTraceLimits,
)
from mini_code_agent.persistence.schema import connect_database
from mini_code_agent.repair.events import (
    RepairAttemptCompleted,
    RepairAttemptStarted,
    RepairEvent,
    RepairStarted,
    RepairStopped,
    RepairVerificationStarted,
)

_REPAIR_EVENT_ADAPTER = TypeAdapter[RepairEvent](RepairEvent)


class SqliteRepairJournal:
    def __init__(
        self,
        database: Path,
        limits: SessionTraceLimits,
        secrets: tuple[str, ...],
    ) -> None:
        self._database = database
        self._limits = limits
        self._secrets = tuple(sorted((value for value in secrets if value), key=len, reverse=True))

    def append(self, event: RepairEvent) -> None:
        payload, payload_json = _encode_event(event, self._secrets)
        if len(payload_json.encode("utf-8")) > self._limits.max_event_bytes:
            raise PersistenceError(
                PersistenceErrorCode.LIMIT_EXCEEDED,
                "Repair event exceeds the configured limit.",
            )
        with connect_database(self._database, self._limits) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                appended = _append_in_transaction(
                    connection,
                    self._limits,
                    event,
                    payload,
                    payload_json,
                )
                if not appended:
                    connection.rollback()
                    return
                connection.commit()
            except PersistenceError:
                _rollback(connection)
                raise
            except (sqlite3.Error, TypeError, ValueError, ValidationError):
                _rollback(connection)
                raise PersistenceError(
                    PersistenceErrorCode.STORAGE_FAILED,
                    "Repair event could not be persisted.",
                ) from None


def get_repair_run(
    database: Path,
    limits: SessionTraceLimits,
    repair_id: str,
) -> RepairRunRecord:
    _validate_identifier(repair_id)
    with connect_database(database, limits) as connection:
        row = connection.execute(
            "SELECT * FROM repair_runs WHERE repair_id = ?",
            (repair_id,),
        ).fetchone()
    if row is None:
        raise PersistenceError(
            PersistenceErrorCode.RUN_NOT_FOUND,
            "Repair Run was not found.",
        )
    return _run_from_row(row)


def read_repair_trace(
    database: Path,
    limits: SessionTraceLimits,
    repair_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 100,
) -> tuple[RepairTraceRecord, ...]:
    _validate_identifier(repair_id)
    if (
        not 0 <= after_sequence <= limits.max_events_per_session
        or not 1 <= limit <= limits.max_query_rows
    ):
        raise PersistenceError(
            PersistenceErrorCode.LIMIT_EXCEEDED,
            "Repair trace query limit is invalid.",
        )
    verify_repair_trace(database, limits, repair_id)
    with connect_database(database, limits) as connection:
        rows = connection.execute(
            """
            SELECT * FROM repair_events
            WHERE repair_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (repair_id, after_sequence, limit),
        ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def verify_repair_trace(
    database: Path,
    limits: SessionTraceLimits,
    repair_id: str,
) -> RepairTraceVerification:
    _validate_identifier(repair_id)
    previous = EMPTY_TRACE_SHA256
    expected_sequence = 1
    previous_timestamp: datetime | None = None
    previous_event: RepairEvent | None = None
    first_event: RepairStarted | None = None
    completed_count = 0
    with connect_database(database, limits) as connection:
        connection.execute("BEGIN")
        run_row = connection.execute(
            "SELECT * FROM repair_runs WHERE repair_id = ?",
            (repair_id,),
        ).fetchone()
        if run_row is None:
            raise PersistenceError(
                PersistenceErrorCode.RUN_NOT_FOUND,
                "Repair Run was not found.",
            )
        run = _run_from_row(run_row)
        cursor = connection.execute(
            """
            SELECT * FROM repair_events
            WHERE repair_id = ?
            ORDER BY sequence ASC
            """,
            (repair_id,),
        )
        while True:
            rows = cursor.fetchmany(limits.max_query_rows)
            if not rows:
                break
            for row in rows:
                record = _record_from_row(row)
                payload = cast(dict[str, object], record.event.model_dump(mode="json"))
                expected_hash = _event_sha256(
                    repair_id=repair_id,
                    sequence=expected_sequence,
                    previous_sha256=previous,
                    event_payload=payload,
                )
                if (
                    record.repair_id != repair_id
                    or record.event.repair_id != repair_id
                    or record.sequence != expected_sequence
                    or record.previous_sha256 != previous
                    or record.event_sha256 != expected_hash
                    or (
                        previous_timestamp is not None
                        and record.event.timestamp < previous_timestamp
                    )
                ):
                    raise _trace_corrupt()
                if not _valid_transition(
                    previous_event,
                    record.event,
                    completed_count=completed_count,
                ):
                    raise _trace_corrupt()
                if isinstance(record.event, RepairStarted):
                    first_event = record.event
                if isinstance(record.event, RepairAttemptCompleted):
                    completed_count += 1
                previous = record.event_sha256
                previous_timestamp = record.event.timestamp
                previous_event = record.event
                expected_sequence += 1
    event_count = expected_sequence - 1
    if (
        event_count != run.event_count
        or run.next_sequence != expected_sequence
        or run.trace_head_sha256 != previous
        or first_event is None
        or run.started_at != first_event.timestamp
        or run.scope_sha256 != first_event.scope_sha256
        or (
            run.status is RepairRunStatus.STOPPED
            and (
                not isinstance(previous_event, RepairStopped)
                or run.stopped_at != previous_event.timestamp
                or run.stop_reason is not previous_event.reason
            )
        )
        or (run.status is RepairRunStatus.ACTIVE and isinstance(previous_event, RepairStopped))
    ):
        raise _trace_corrupt()
    return RepairTraceVerification(
        repair_id=repair_id,
        event_count=event_count,
        trace_head_sha256=previous,
    )


def _append_in_transaction(
    connection: sqlite3.Connection,
    limits: SessionTraceLimits,
    event: RepairEvent,
    payload: dict[str, object],
    payload_json: str,
) -> bool:
    duplicate = connection.execute(
        "SELECT repair_id, payload_json FROM repair_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    if duplicate is not None:
        if (
            str(duplicate["repair_id"]) == event.repair_id
            and str(duplicate["payload_json"]) == payload_json
        ):
            return False
        raise PersistenceError(
            PersistenceErrorCode.EVENT_CONFLICT,
            "Repair event identifier conflicts with stored data.",
        )

    row = connection.execute(
        "SELECT * FROM repair_runs WHERE repair_id = ?",
        (event.repair_id,),
    ).fetchone()
    if isinstance(event, RepairStarted):
        if row is not None:
            raise PersistenceError(
                PersistenceErrorCode.INVALID_TRANSITION,
                "Repair Run already exists.",
            )
        _create_run(connection, event)
        row = connection.execute(
            "SELECT * FROM repair_runs WHERE repair_id = ?",
            (event.repair_id,),
        ).fetchone()
    elif row is None:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Repair event requires an active Repair Run.",
        )
    if row is None:
        raise _trace_corrupt()
    run = _run_from_row(row)
    if run.status is not RepairRunStatus.ACTIVE:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Repair Run is not active.",
        )
    if run.event_count >= limits.max_events_per_session:
        raise PersistenceError(
            PersistenceErrorCode.LIMIT_EXCEEDED,
            "Repair trace reached the configured event limit.",
        )
    _validate_head(connection, run)
    _validate_transition(connection, run, event)

    sequence = run.next_sequence
    current_sha256 = _event_sha256(
        repair_id=event.repair_id,
        sequence=sequence,
        previous_sha256=run.trace_head_sha256,
        event_payload=payload,
    )
    connection.execute(
        """
        INSERT INTO repair_events (
            repair_id, sequence, schema_version, event_id, event_type,
            event_timestamp, payload_json, previous_sha256, event_sha256
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.repair_id,
            sequence,
            event.event_id,
            event.type,
            cast(str, payload["timestamp"]),
            payload_json,
            run.trace_head_sha256,
            current_sha256,
        ),
    )
    stopped_at = event.timestamp.isoformat() if isinstance(event, RepairStopped) else None
    status = (
        RepairRunStatus.STOPPED.value
        if isinstance(event, RepairStopped)
        else RepairRunStatus.ACTIVE.value
    )
    stop_reason = event.reason.value if isinstance(event, RepairStopped) else None
    updated = connection.execute(
        """
        UPDATE repair_runs
        SET stopped_at = ?,
            status = ?,
            stop_reason = ?,
            event_count = ?,
            next_sequence = ?,
            trace_head_sha256 = ?
        WHERE repair_id = ?
          AND status = 'active'
          AND event_count = ?
          AND next_sequence = ?
          AND trace_head_sha256 = ?
        """,
        (
            stopped_at,
            status,
            stop_reason,
            run.event_count + 1,
            sequence + 1,
            current_sha256,
            event.repair_id,
            run.event_count,
            run.next_sequence,
            run.trace_head_sha256,
        ),
    )
    if updated.rowcount != 1:
        raise _trace_corrupt()
    return True


def _create_run(connection: sqlite3.Connection, event: RepairStarted) -> None:
    connection.execute(
        """
        INSERT INTO repair_runs (
            repair_id, started_at, stopped_at, status, stop_reason,
            scope_sha256, event_count, next_sequence, trace_head_sha256
        ) VALUES (?, ?, NULL, 'active', NULL, ?, 0, 1, ?)
        """,
        (
            event.repair_id,
            event.timestamp.isoformat(),
            event.scope_sha256,
            EMPTY_TRACE_SHA256,
        ),
    )


def _validate_head(
    connection: sqlite3.Connection,
    run: RepairRunRecord,
) -> None:
    if run.event_count == 0:
        if run.trace_head_sha256 != EMPTY_TRACE_SHA256:
            raise _trace_corrupt()
        return
    row = connection.execute(
        """
        SELECT sequence, event_sha256 FROM repair_events
        WHERE repair_id = ?
        ORDER BY sequence DESC LIMIT 1
        """,
        (run.repair_id,),
    ).fetchone()
    if (
        row is None
        or int(row["sequence"]) != run.event_count
        or str(row["event_sha256"]) != run.trace_head_sha256
    ):
        raise _trace_corrupt()


def _validate_transition(
    connection: sqlite3.Connection,
    run: RepairRunRecord,
    event: RepairEvent,
) -> None:
    last_row = connection.execute(
        """
        SELECT payload_json FROM repair_events
        WHERE repair_id = ?
        ORDER BY sequence DESC LIMIT 1
        """,
        (run.repair_id,),
    ).fetchone()
    last = _decode_event(str(last_row["payload_json"])) if last_row is not None else None
    if event.timestamp < run.started_at or (last is not None and event.timestamp < last.timestamp):
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Repair event order is invalid.",
        )
    completed_count = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM repair_events
            WHERE repair_id = ? AND event_type = 'repair_attempt_completed'
            """,
            (run.repair_id,),
        ).fetchone()[0]
    )
    if not _valid_transition(last, event, completed_count=completed_count):
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Repair event transition is invalid.",
        )


def _valid_transition(
    last: RepairEvent | None,
    event: RepairEvent,
    *,
    completed_count: int,
) -> bool:
    if isinstance(event, RepairStarted):
        return last is None and completed_count == 0
    if isinstance(event, RepairStopped):
        return last is not None and event.attempts == completed_count
    if isinstance(event, RepairAttemptStarted):
        return (
            isinstance(last, (RepairStarted, RepairAttemptCompleted))
            and event.attempt == completed_count + 1
        )
    if isinstance(event, RepairVerificationStarted):
        return isinstance(last, RepairAttemptStarted) and event.attempt == last.attempt
    return (
        isinstance(last, RepairVerificationStarted)
        and event.attempt == last.attempt
        and event.patch_sha256 == last.patch_sha256
        and event.patch_bytes == last.patch_bytes
    )


def _encode_event(
    event: RepairEvent,
    secrets: tuple[str, ...],
) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], event.model_dump(mode="json"))
    if isinstance(event, RepairStopped) and isinstance(payload.get("error"), str):
        error = cast(str, payload["error"])
        for secret in secrets:
            error = error.replace(secret, "***")
        payload["error"] = error
    return payload, canonical_json(payload)


def _decode_event(payload_json: str) -> RepairEvent:
    try:
        payload = json.loads(payload_json)
        return _REPAIR_EVENT_ADAPTER.validate_python(payload)
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
        raise _trace_corrupt() from None


def _record_from_row(row: sqlite3.Row) -> RepairTraceRecord:
    try:
        event = _decode_event(str(row["payload_json"]))
        if int(row["schema_version"]) != 1:
            raise ValueError
        if (
            event.event_id != str(row["event_id"])
            or event.repair_id != str(row["repair_id"])
            or event.type != str(row["event_type"])
            or event.timestamp != datetime.fromisoformat(str(row["event_timestamp"]))
        ):
            raise ValueError
        return RepairTraceRecord(
            schema_version=1,
            sequence=int(row["sequence"]),
            repair_id=str(row["repair_id"]),
            event=event,
            previous_sha256=str(row["previous_sha256"]),
            event_sha256=str(row["event_sha256"]),
        )
    except (TypeError, ValueError, ValidationError, PersistenceError):
        raise _trace_corrupt() from None


def _run_from_row(row: sqlite3.Row) -> RepairRunRecord:
    try:
        return RepairRunRecord.model_validate(dict(row))
    except (TypeError, ValueError, ValidationError):
        raise _trace_corrupt() from None


def _event_sha256(
    *,
    repair_id: str,
    sequence: int,
    previous_sha256: str,
    event_payload: dict[str, object],
) -> str:
    envelope = {
        "event": event_payload,
        "previous_sha256": previous_sha256,
        "repair_id": repair_id,
        "schema_version": 1,
        "sequence": sequence,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


def _validate_identifier(identifier: str) -> None:
    if re.fullmatch(IDENTIFIER_PATTERN, identifier) is None:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_IDENTIFIER,
            "Repair identifier is invalid.",
        )


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        return


def _trace_corrupt() -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.TRACE_CORRUPT,
        "Repair trace integrity check failed.",
    )
