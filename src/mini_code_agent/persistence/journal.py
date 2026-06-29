from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from mini_code_agent.agent.events import AgentEvent, RunStarted, RunStopped
from mini_code_agent.agent.models import StopReason
from mini_code_agent.persistence.codec import encode_event, event_sha256
from mini_code_agent.persistence.errors import (
    PersistenceError,
    PersistenceErrorCode,
)
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    TRACE_SCHEMA_VERSION,
    RunStatus,
    SessionRecord,
    SessionStatus,
    SessionTraceLimits,
)
from mini_code_agent.persistence.schema import connect_database


class SessionEventJournal:
    def __init__(
        self,
        database: Path,
        limits: SessionTraceLimits,
        session_id: str,
        secrets: tuple[str, ...],
    ) -> None:
        self._database = database
        self._limits = limits
        self._session_id = session_id
        self._secrets = secrets

    def append(self, event: AgentEvent) -> None:
        payload, payload_json = encode_event(event, self._secrets)
        if len(payload_json.encode("utf-8")) > self._limits.max_event_bytes:
            raise PersistenceError(
                PersistenceErrorCode.LIMIT_EXCEEDED,
                "Trace event exceeds the configured limit.",
            )

        with connect_database(self._database, self._limits) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                session = _load_session(connection, self._session_id)
                duplicate = connection.execute(
                    """
                    SELECT session_id, payload_json
                    FROM trace_events
                    WHERE event_id = ?
                    """,
                    (event.event_id,),
                ).fetchone()
                if duplicate is not None:
                    if (
                        str(duplicate["session_id"]) == self._session_id
                        and str(duplicate["payload_json"]) == payload_json
                    ):
                        connection.rollback()
                        return
                    raise PersistenceError(
                        PersistenceErrorCode.EVENT_CONFLICT,
                        "Trace event identifier conflicts with stored data.",
                    )
                if session.event_count >= self._limits.max_events_per_session:
                    raise PersistenceError(
                        PersistenceErrorCode.LIMIT_EXCEEDED,
                        "Session trace reached the configured event limit.",
                    )
                _validate_trace_head(connection, session)
                _validate_event_time(session, event.timestamp)
                _apply_projection(connection, self._session_id, session, event)

                sequence = session.next_sequence
                current_sha256 = event_sha256(
                    session_id=self._session_id,
                    sequence=sequence,
                    previous_sha256=session.trace_head_sha256,
                    event_payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO trace_events (
                        session_id,
                        sequence,
                        schema_version,
                        run_id,
                        event_id,
                        event_type,
                        event_timestamp,
                        payload_json,
                        previous_sha256,
                        event_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._session_id,
                        sequence,
                        TRACE_SCHEMA_VERSION,
                        event.run_id,
                        event.event_id,
                        event.type,
                        cast(str, payload["timestamp"]),
                        payload_json,
                        session.trace_head_sha256,
                        current_sha256,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE sessions
                    SET updated_at = ?,
                        event_count = ?,
                        next_sequence = ?,
                        trace_head_sha256 = ?
                    WHERE session_id = ?
                      AND event_count = ?
                      AND next_sequence = ?
                      AND trace_head_sha256 = ?
                    """,
                    (
                        cast(str, payload["timestamp"]),
                        session.event_count + 1,
                        session.next_sequence + 1,
                        current_sha256,
                        self._session_id,
                        session.event_count,
                        session.next_sequence,
                        session.trace_head_sha256,
                    ),
                )
                if updated.rowcount != 1:
                    raise _trace_corrupt()
                connection.commit()
            except PersistenceError:
                _rollback(connection)
                raise
            except sqlite3.Error:
                _rollback(connection)
                raise PersistenceError(
                    PersistenceErrorCode.STORAGE_FAILED,
                    "Trace event could not be persisted.",
                ) from None


def _load_session(
    connection: sqlite3.Connection,
    session_id: str,
) -> SessionRecord:
    row = connection.execute(
        "SELECT * FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise PersistenceError(
            PersistenceErrorCode.SESSION_NOT_FOUND,
            "Session was not found.",
        )
    try:
        return SessionRecord.model_validate(dict(row))
    except (TypeError, ValueError):
        raise _trace_corrupt() from None


def _validate_trace_head(
    connection: sqlite3.Connection,
    session: SessionRecord,
) -> None:
    if session.event_count == 0:
        if session.trace_head_sha256 != EMPTY_TRACE_SHA256:
            raise _trace_corrupt()
        return
    row = connection.execute(
        """
        SELECT sequence, event_sha256
        FROM trace_events
        WHERE session_id = ?
        ORDER BY sequence DESC
        LIMIT 1
        """,
        (session.session_id,),
    ).fetchone()
    if (
        row is None
        or int(row["sequence"]) != session.event_count
        or str(row["event_sha256"]) != session.trace_head_sha256
    ):
        raise _trace_corrupt()


def _validate_event_time(
    session: SessionRecord,
    timestamp: datetime,
) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Trace event timestamp is invalid.",
        )
    if timestamp < session.updated_at:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Trace event order is invalid.",
        )


def _apply_projection(
    connection: sqlite3.Connection,
    session_id: str,
    session: SessionRecord,
    event: AgentEvent,
) -> None:
    if isinstance(event, RunStarted):
        _start_run(connection, session_id, session, event)
        return

    run = connection.execute(
        """
        SELECT status, started_at
        FROM runs
        WHERE session_id = ? AND run_id = ?
        """,
        (session_id, event.run_id),
    ).fetchone()
    if run is None or str(run["status"]) != RunStatus.ACTIVE.value:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Trace event is not valid for the current Run.",
        )
    try:
        started_at = datetime.fromisoformat(str(run["started_at"]))
    except ValueError:
        raise _trace_corrupt() from None
    if event.timestamp < started_at:
        raise PersistenceError(
            PersistenceErrorCode.INVALID_TRANSITION,
            "Trace event order is invalid.",
        )
    if isinstance(event, RunStopped):
        _stop_run(connection, session_id, event)


def _start_run(
    connection: sqlite3.Connection,
    session_id: str,
    session: SessionRecord,
    event: RunStarted,
) -> None:
    if session.status is SessionStatus.ACTIVE:
        raise PersistenceError(
            PersistenceErrorCode.RUN_CONFLICT,
            "Session already has an active Run.",
        )
    existing = connection.execute(
        "SELECT 1 FROM runs WHERE run_id = ?",
        (event.run_id,),
    ).fetchone()
    if existing is not None:
        raise PersistenceError(
            PersistenceErrorCode.RUN_CONFLICT,
            "Run already exists.",
        )
    timestamp = event.timestamp.isoformat()
    connection.execute(
        """
        INSERT INTO runs (
            run_id,
            session_id,
            started_at,
            stopped_at,
            status,
            stop_reason,
            turns,
            tool_calls,
            input_tokens,
            output_tokens
        ) VALUES (?, ?, ?, NULL, ?, NULL, 0, 0, 0, 0)
        """,
        (
            event.run_id,
            session_id,
            timestamp,
            RunStatus.ACTIVE.value,
        ),
    )
    connection.execute(
        """
        UPDATE sessions
        SET status = ?, last_run_id = ?
        WHERE session_id = ?
        """,
        (SessionStatus.ACTIVE.value, event.run_id, session_id),
    )


def _stop_run(
    connection: sqlite3.Connection,
    session_id: str,
    event: RunStopped,
) -> None:
    completed = event.reason is StopReason.COMPLETED
    run_status = RunStatus.COMPLETED if completed else RunStatus.STOPPED
    session_status = SessionStatus.COMPLETED if completed else SessionStatus.STOPPED
    connection.execute(
        """
        UPDATE runs
        SET stopped_at = ?,
            status = ?,
            stop_reason = ?,
            turns = ?,
            tool_calls = ?,
            input_tokens = ?,
            output_tokens = ?
        WHERE session_id = ? AND run_id = ? AND status = ?
        """,
        (
            event.timestamp.isoformat(),
            run_status.value,
            event.reason.value,
            event.turns,
            event.tool_calls,
            event.usage.input_tokens,
            event.usage.output_tokens,
            session_id,
            event.run_id,
            RunStatus.ACTIVE.value,
        ),
    )
    connection.execute(
        """
        UPDATE sessions
        SET status = ?
        WHERE session_id = ?
        """,
        (session_status.value, session_id),
    )


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        return


def _trace_corrupt() -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.TRACE_CORRUPT,
        "Session trace integrity check failed.",
    )
