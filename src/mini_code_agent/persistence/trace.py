from __future__ import annotations

import sqlite3
from pathlib import Path

from mini_code_agent.persistence.codec import (
    decode_event,
    event_sha256,
    trace_corrupt,
)
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    TRACE_SCHEMA_VERSION,
    SessionRecord,
    SessionTraceLimits,
    TraceRecord,
    TraceVerification,
)
from mini_code_agent.persistence.schema import connect_database


def read_trace_records(
    database: Path,
    limits: SessionTraceLimits,
    session_id: str,
    *,
    after_sequence: int,
    limit: int,
) -> tuple[TraceRecord, ...]:
    with connect_database(database, limits) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM trace_events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (session_id, after_sequence, limit),
        ).fetchall()
    return tuple(_decode_row(row)[0] for row in rows)


def verify_session_trace(
    database: Path,
    limits: SessionTraceLimits,
    session: SessionRecord,
) -> TraceVerification:
    expected_sequence = 1
    expected_previous = EMPTY_TRACE_SHA256
    event_count = 0
    after_sequence = 0

    with connect_database(database, limits) as connection:
        while True:
            rows = connection.execute(
                """
                SELECT *
                FROM trace_events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (
                    session.session_id,
                    after_sequence,
                    limits.max_query_rows,
                ),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                record, payload = _decode_row(row)
                if (
                    record.sequence != expected_sequence
                    or record.previous_sha256 != expected_previous
                ):
                    raise trace_corrupt()
                recomputed = event_sha256(
                    session_id=session.session_id,
                    sequence=record.sequence,
                    previous_sha256=record.previous_sha256,
                    event_payload=payload,
                )
                if recomputed != record.event_sha256:
                    raise trace_corrupt()
                event_count += 1
                expected_sequence += 1
                expected_previous = record.event_sha256
                after_sequence = record.sequence
            if len(rows) < limits.max_query_rows:
                break

    if (
        event_count != session.event_count
        or expected_sequence != session.next_sequence
        or expected_previous != session.trace_head_sha256
    ):
        raise trace_corrupt()
    return TraceVerification(
        session_id=session.session_id,
        event_count=event_count,
        trace_head_sha256=expected_previous,
    )


def _decode_row(
    row: sqlite3.Row,
) -> tuple[TraceRecord, dict[str, object]]:
    try:
        schema_version = int(row["schema_version"])
        sequence = int(row["sequence"])
        session_id = str(row["session_id"])
        run_id = str(row["run_id"])
        event_id = str(row["event_id"])
        event_type = str(row["event_type"])
        event_timestamp = str(row["event_timestamp"])
        payload_json = str(row["payload_json"])
        previous_sha256 = str(row["previous_sha256"])
        current_sha256 = str(row["event_sha256"])
    except (IndexError, TypeError, ValueError):
        raise trace_corrupt() from None
    if schema_version != TRACE_SCHEMA_VERSION:
        raise trace_corrupt()

    payload, event = decode_event(payload_json)
    if (
        event.run_id != run_id
        or event.event_id != event_id
        or event.type != event_type
        or payload.get("timestamp") != event_timestamp
    ):
        raise trace_corrupt()
    try:
        record = TraceRecord(
            schema_version=schema_version,
            sequence=sequence,
            session_id=session_id,
            event=event,
            previous_sha256=previous_sha256,
            event_sha256=current_sha256,
        )
    except (TypeError, ValueError):
        raise trace_corrupt() from None
    return record, payload
