from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import SessionTraceLimits
from mini_code_agent.persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    connect_database,
    initialize_database,
)


def test_schema_initializes_versioned_tables_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    initialize_database(database, SessionTraceLimits())

    with closing(sqlite3.connect(database)) as connection, connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    initialize_database(database, SessionTraceLimits())

    assert version == DATABASE_SCHEMA_VERSION == 2
    assert {"sessions", "runs", "trace_events", "checkpoints"} <= tables


def test_configured_connection_enables_sqlite_safety_pragmas(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    limits = SessionTraceLimits(busy_timeout_ms=321)
    initialize_database(database, limits)

    with connect_database(database, limits) as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        row = connection.execute("SELECT 1 AS value").fetchone()

    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert synchronous == 2
    assert busy_timeout == 321
    assert row["value"] == 1


def test_schema_rejects_unsupported_future_version(tmp_path: Path) -> None:
    database = tmp_path / "future.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA user_version = 3")

    with pytest.raises(PersistenceError) as captured:
        initialize_database(database, SessionTraceLimits())

    assert captured.value.code.value == "unsupported_schema"
    assert str(database) not in captured.value.public_message


def test_schema_migrates_v1_without_rewriting_existing_data(tmp_path: Path) -> None:
    database = tmp_path / "v1.db"
    initialize_database(database, SessionTraceLimits())
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            INSERT INTO sessions (
                session_id, schema_version, created_at, updated_at, status,
                last_run_id, event_count, next_sequence, trace_head_sha256
            ) VALUES ('session-1', 1, ?, ?, 'ready', NULL, 0, 1, ?)
            """,
            (
                "2026-06-30T00:00:00+00:00",
                "2026-06-30T00:00:00+00:00",
                "0" * 64,
            ),
        )
        connection.execute("DROP TABLE checkpoints")
        connection.execute("PRAGMA user_version = 1")

    initialize_database(database, SessionTraceLimits())

    with closing(sqlite3.connect(database)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        row = connection.execute("SELECT session_id, schema_version FROM sessions").fetchone()
        checkpoint_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'checkpoints'"
        ).fetchone()
    assert version == 2
    assert row == ("session-1", 1)
    assert checkpoint_sql is not None


def test_schema_migration_rolls_back_new_objects_and_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conflict.db"
    initialize_database(database, SessionTraceLimits())
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE checkpoints")
        connection.execute("CREATE TABLE checkpoints_session_created_idx (value TEXT)")
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(PersistenceError) as captured:
        initialize_database(database, SessionTraceLimits())

    with closing(sqlite3.connect(database)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        checkpoint = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'checkpoints'"
        ).fetchone()
    assert captured.value.code is PersistenceErrorCode.STORAGE_FAILED
    assert version == 1
    assert checkpoint is None


def test_schema_rejects_directory_without_leaking_path(tmp_path: Path) -> None:
    database = tmp_path / "database-directory"
    database.mkdir()

    with pytest.raises(PersistenceError) as captured:
        initialize_database(database, SessionTraceLimits())

    assert captured.value.code.value == "database_unavailable"
    assert str(database) not in captured.value.public_message
