from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from mini_code_agent.persistence.errors import (
    PersistenceError,
    PersistenceErrorCode,
)
from mini_code_agent.persistence.models import SCHEMA_VERSION, SessionTraceLimits

_REQUIRED_TABLES = frozenset({"sessions", "runs", "trace_events"})

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ready', 'active', 'completed', 'stopped')),
        last_run_id TEXT,
        event_count INTEGER NOT NULL CHECK (event_count >= 0),
        next_sequence INTEGER NOT NULL CHECK (next_sequence >= 1),
        trace_head_sha256 TEXT NOT NULL CHECK (length(trace_head_sha256) = 64)
    )
    """,
    """
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        stopped_at TEXT,
        status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'stopped')),
        stop_reason TEXT,
        turns INTEGER NOT NULL CHECK (turns >= 0),
        tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
        input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
        FOREIGN KEY (session_id) REFERENCES sessions(session_id),
        UNIQUE (session_id, run_id)
    )
    """,
    """
    CREATE TABLE trace_events (
        session_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        run_id TEXT NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        event_timestamp TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_sha256 TEXT NOT NULL CHECK (length(previous_sha256) = 64),
        event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
        PRIMARY KEY (session_id, sequence),
        FOREIGN KEY (session_id) REFERENCES sessions(session_id),
        FOREIGN KEY (session_id, run_id) REFERENCES runs(session_id, run_id)
    )
    """,
    """
    CREATE INDEX sessions_created_idx
    ON sessions(created_at DESC, session_id ASC)
    """,
    """
    CREATE INDEX runs_session_started_idx
    ON runs(session_id, started_at DESC, run_id ASC)
    """,
    """
    CREATE INDEX trace_session_type_sequence_idx
    ON trace_events(session_id, event_type, sequence)
    """,
)


@contextmanager
def connect_database(
    database: Path,
    limits: SessionTraceLimits,
) -> Generator[sqlite3.Connection]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            database,
            timeout=limits.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(f"PRAGMA busy_timeout = {limits.busy_timeout_ms}")
        yield connection
    except PersistenceError:
        raise
    except (OSError, sqlite3.Error):
        raise PersistenceError(
            PersistenceErrorCode.DATABASE_UNAVAILABLE,
            "Session database is unavailable.",
        ) from None
    finally:
        if connection is not None:
            connection.close()


def initialize_database(
    database: Path,
    limits: SessionTraceLimits,
) -> None:
    if database.exists() and (not database.is_file() or database.is_symlink()):
        raise PersistenceError(
            PersistenceErrorCode.DATABASE_UNAVAILABLE,
            "Session database is unavailable.",
        )
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise PersistenceError(
            PersistenceErrorCode.DATABASE_UNAVAILABLE,
            "Session database is unavailable.",
        ) from None

    with connect_database(database, limits) as connection:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, SCHEMA_VERSION):
                raise PersistenceError(
                    PersistenceErrorCode.UNSUPPORTED_SCHEMA,
                    "Session database schema is unsupported.",
                )
            if version == 0:
                _create_schema(connection)
            _verify_schema(connection)
        except PersistenceError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise PersistenceError(
                PersistenceErrorCode.STORAGE_FAILED,
                "Session database could not be initialized.",
            ) from None


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _verify_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    tables = {str(row["name"]) for row in rows}
    if not _REQUIRED_TABLES.issubset(tables):
        raise PersistenceError(
            PersistenceErrorCode.STORAGE_FAILED,
            "Session database schema is invalid.",
        )
