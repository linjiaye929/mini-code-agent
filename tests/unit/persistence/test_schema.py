from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mini_code_agent.persistence.errors import PersistenceError
from mini_code_agent.persistence.models import SessionTraceLimits
from mini_code_agent.persistence.schema import connect_database, initialize_database


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

    assert version == 1
    assert {"sessions", "runs", "trace_events"} <= tables


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
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(PersistenceError) as captured:
        initialize_database(database, SessionTraceLimits())

    assert captured.value.code.value == "unsupported_schema"
    assert str(database) not in captured.value.public_message


def test_schema_rejects_directory_without_leaking_path(tmp_path: Path) -> None:
    database = tmp_path / "database-directory"
    database.mkdir()

    with pytest.raises(PersistenceError) as captured:
        initialize_database(database, SessionTraceLimits())

    assert captured.value.code.value == "database_unavailable"
    assert str(database) not in captured.value.public_message
