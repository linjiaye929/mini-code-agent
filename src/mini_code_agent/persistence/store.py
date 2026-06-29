from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import uuid4

from pydantic import SecretStr, ValidationError

from mini_code_agent.checkpoint.models import CheckpointLimits, CheckpointSnapshot
from mini_code_agent.persistence.checkpoints import (
    SessionCheckpointJournal,
    checkpoint_from_row,
)
from mini_code_agent.persistence.errors import (
    PersistenceError,
    PersistenceErrorCode,
)
from mini_code_agent.persistence.journal import SessionEventJournal
from mini_code_agent.persistence.models import (
    EMPTY_TRACE_SHA256,
    IDENTIFIER_PATTERN,
    TRACE_SCHEMA_VERSION,
    RunRecord,
    SessionRecord,
    SessionStatus,
    SessionTraceLimits,
    TraceRecord,
    TraceVerification,
)
from mini_code_agent.persistence.schema import connect_database, initialize_database
from mini_code_agent.persistence.trace import (
    read_trace_records,
    verify_session_trace,
)


class SqliteSessionTraceStore:
    def __init__(
        self,
        database: Path,
        *,
        limits: SessionTraceLimits | None = None,
        checkpoint_limits: CheckpointLimits | None = None,
        secrets: Iterable[str | SecretStr] = (),
    ) -> None:
        self._database = database
        self._limits = limits or SessionTraceLimits()
        self._checkpoint_limits = checkpoint_limits or CheckpointLimits()
        self._secrets = _normalize_secrets(secrets)
        self._initialized = False

    @property
    def limits(self) -> SessionTraceLimits:
        return self._limits

    def initialize(self) -> Self:
        initialize_database(self._database, self._limits)
        self._initialized = True
        return self

    def close(self) -> None:
        self._initialized = False

    def __enter__(self) -> Self:
        return self.initialize()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def create_session(self, session_id: str | None = None) -> SessionRecord:
        self._ensure_initialized()
        identifier = session_id or str(uuid4())
        self._validate_identifier(identifier)
        now = datetime.now(UTC).isoformat()
        with connect_database(self._database, self._limits) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id,
                        schema_version,
                        created_at,
                        updated_at,
                        status,
                        last_run_id,
                        event_count,
                        next_sequence,
                        trace_head_sha256
                    ) VALUES (?, ?, ?, ?, ?, NULL, 0, 1, ?)
                    """,
                    (
                        identifier,
                        TRACE_SCHEMA_VERSION,
                        now,
                        now,
                        SessionStatus.READY.value,
                        EMPTY_TRACE_SHA256,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                raise PersistenceError(
                    PersistenceErrorCode.SESSION_EXISTS,
                    "Session already exists.",
                ) from None
            except sqlite3.Error:
                connection.rollback()
                raise PersistenceError(
                    PersistenceErrorCode.STORAGE_FAILED,
                    "Session could not be created.",
                ) from None
        return self.get_session(identifier)

    def get_session(self, session_id: str) -> SessionRecord:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        with connect_database(self._database, self._limits) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError(
                PersistenceErrorCode.SESSION_NOT_FOUND,
                "Session was not found.",
            )
        return _session_from_row(row)

    def list_sessions(self, *, limit: int = 100) -> tuple[SessionRecord, ...]:
        self._ensure_initialized()
        self._validate_query_limit(limit)
        with connect_database(self._database, self._limits) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM sessions
                ORDER BY created_at DESC, session_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(_session_from_row(row) for row in rows)

    def get_run(self, session_id: str, run_id: str) -> RunRecord:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self._validate_identifier(run_id)
        with connect_database(self._database, self._limits) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE session_id = ? AND run_id = ?
                """,
                (session_id, run_id),
            ).fetchone()
            session_exists = _session_exists(connection, session_id)
        if not session_exists:
            raise PersistenceError(
                PersistenceErrorCode.SESSION_NOT_FOUND,
                "Session was not found.",
            )
        if row is None:
            raise PersistenceError(
                PersistenceErrorCode.RUN_NOT_FOUND,
                "Run was not found.",
            )
        return _run_from_row(row)

    def list_runs(
        self,
        session_id: str,
        *,
        limit: int = 100,
    ) -> tuple[RunRecord, ...]:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self._validate_query_limit(limit)
        with connect_database(self._database, self._limits) as connection:
            if not _session_exists(connection, session_id):
                raise PersistenceError(
                    PersistenceErrorCode.SESSION_NOT_FOUND,
                    "Session was not found.",
                )
            rows = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE session_id = ?
                ORDER BY started_at DESC, run_id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return tuple(_run_from_row(row) for row in rows)

    def journal(self, session_id: str) -> SessionEventJournal:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self.get_session(session_id)
        return SessionEventJournal(
            self._database,
            self._limits,
            session_id,
            self._secrets,
        )

    def checkpoints(self, session_id: str) -> SessionCheckpointJournal:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self.get_session(session_id)
        return SessionCheckpointJournal(
            self._database,
            self._limits,
            self._checkpoint_limits,
            session_id,
            self._secrets,
        )

    def get_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
    ) -> CheckpointSnapshot:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self._validate_identifier(checkpoint_id)
        with connect_database(self._database, self._limits) as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? AND checkpoint_id = ?",
                (session_id, checkpoint_id),
            ).fetchone()
        if row is None:
            raise PersistenceError(
                PersistenceErrorCode.CHECKPOINT_NOT_FOUND,
                "Checkpoint was not found.",
            )
        return checkpoint_from_row(row)

    def list_checkpoints(
        self,
        session_id: str,
        *,
        limit: int = 100,
    ) -> tuple[CheckpointSnapshot, ...]:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self._validate_query_limit(limit)
        self.get_session(session_id)
        with connect_database(self._database, self._limits) as connection:
            rows = connection.execute(
                """
                SELECT * FROM checkpoints
                WHERE session_id = ?
                ORDER BY created_at DESC, checkpoint_id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return tuple(checkpoint_from_row(row) for row in rows)

    def latest_checkpoint(self, session_id: str) -> CheckpointSnapshot:
        checkpoints = self.list_checkpoints(session_id, limit=1)
        if not checkpoints:
            raise PersistenceError(
                PersistenceErrorCode.CHECKPOINT_NOT_FOUND,
                "Checkpoint was not found.",
            )
        return checkpoints[0]

    def read_trace(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TraceRecord, ...]:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        self._validate_query_limit(limit)
        if not 0 <= after_sequence <= self._limits.max_events_per_session:
            raise PersistenceError(
                PersistenceErrorCode.LIMIT_EXCEEDED,
                "Trace query sequence is invalid.",
            )
        self.get_session(session_id)
        return read_trace_records(
            self._database,
            self._limits,
            session_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def verify_trace(self, session_id: str) -> TraceVerification:
        self._ensure_initialized()
        self._validate_identifier(session_id)
        session = self.get_session(session_id)
        return verify_session_trace(
            self._database,
            self._limits,
            session,
        )

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise PersistenceError(
                PersistenceErrorCode.STORAGE_FAILED,
                "Session store is not initialized.",
            )

    def _validate_query_limit(self, limit: int) -> None:
        if not 1 <= limit <= self._limits.max_query_rows:
            raise PersistenceError(
                PersistenceErrorCode.LIMIT_EXCEEDED,
                "Session query limit is invalid.",
            )

    @staticmethod
    def _validate_identifier(identifier: str) -> None:
        if re.fullmatch(IDENTIFIER_PATTERN, identifier) is None:
            raise PersistenceError(
                PersistenceErrorCode.INVALID_IDENTIFIER,
                "Session identifier is invalid.",
            )


def _session_exists(connection: sqlite3.Connection, session_id: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        is not None
    )


def _session_from_row(row: sqlite3.Row) -> SessionRecord:
    try:
        return SessionRecord.model_validate(dict(row))
    except (TypeError, ValueError, ValidationError):
        raise PersistenceError(
            PersistenceErrorCode.TRACE_CORRUPT,
            "Session database contains invalid data.",
        ) from None


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    try:
        return RunRecord.model_validate(dict(row))
    except (TypeError, ValueError, ValidationError):
        raise PersistenceError(
            PersistenceErrorCode.TRACE_CORRUPT,
            "Session database contains invalid data.",
        ) from None


def _normalize_secrets(
    secrets: Iterable[str | SecretStr],
) -> tuple[str, ...]:
    values: set[str] = set()
    for secret in secrets:
        value = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        if value:
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))
