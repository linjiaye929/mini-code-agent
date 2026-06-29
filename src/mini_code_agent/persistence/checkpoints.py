from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from mini_code_agent.agent.events import CheckpointSaved
from mini_code_agent.checkpoint.codec import (
    canonical_json,
    encode_draft,
    payload_sha256,
    transcript_sha256,
)
from mini_code_agent.checkpoint.models import (
    CHECKPOINT_FORMAT_VERSION,
    CheckpointDraft,
    CheckpointLimits,
    CheckpointSnapshot,
)
from mini_code_agent.persistence.codec import encode_event
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.journal import append_event_in_transaction
from mini_code_agent.persistence.models import RunStatus, SessionTraceLimits
from mini_code_agent.persistence.schema import connect_database


class SessionCheckpointJournal:
    def __init__(
        self,
        database: Path,
        trace_limits: SessionTraceLimits,
        checkpoint_limits: CheckpointLimits,
        session_id: str,
        secrets: tuple[str, ...],
    ) -> None:
        self._database = database
        self._trace_limits = trace_limits
        self._checkpoint_limits = checkpoint_limits
        self._session_id = session_id
        self._secrets = secrets

    def save(self, draft: CheckpointDraft) -> CheckpointSnapshot:
        _, draft_json = encode_draft(draft)
        if (
            len(draft.messages) > self._checkpoint_limits.max_messages
            or len(draft_json.encode("utf-8")) > self._checkpoint_limits.max_payload_bytes
        ):
            raise _limit_exceeded()

        with connect_database(self._database, self._trace_limits) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = _select_checkpoint(connection, self._session_id, draft.checkpoint_id)
                if existing is not None:
                    saved = checkpoint_from_row(existing)
                    _, saved_draft_json = encode_draft(snapshot_to_draft(saved))
                    if saved_draft_json != draft_json:
                        raise PersistenceError(
                            PersistenceErrorCode.CHECKPOINT_CONFLICT,
                            "Checkpoint identifier conflicts with stored data.",
                        )
                    connection.rollback()
                    return saved
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM checkpoints WHERE session_id = ?",
                        (self._session_id,),
                    ).fetchone()[0]
                )
                if count >= self._checkpoint_limits.max_checkpoints_per_session:
                    raise _limit_exceeded()
                run = connection.execute(
                    "SELECT status FROM runs WHERE session_id = ? AND run_id = ?",
                    (self._session_id, draft.source_run_id),
                ).fetchone()
                if run is None or str(run["status"]) != RunStatus.ACTIVE.value:
                    raise PersistenceError(
                        PersistenceErrorCode.INVALID_TRANSITION,
                        "Checkpoint source Run is not active.",
                    )
                event = CheckpointSaved(
                    run_id=draft.source_run_id,
                    timestamp=draft.created_at,
                    checkpoint_id=draft.checkpoint_id,
                    turn=draft.turns,
                    message_count=len(draft.messages),
                    transcript_sha256=transcript_sha256(draft.messages),
                )
                event_payload, event_json = encode_event(event, self._secrets)
                appended = append_event_in_transaction(
                    connection,
                    self._trace_limits,
                    self._session_id,
                    event,
                    event_payload,
                    event_json,
                )
                if appended is None:
                    raise PersistenceError(
                        PersistenceErrorCode.CHECKPOINT_CONFLICT,
                        "Checkpoint identifier conflicts with stored data.",
                    )
                sequence, trace_head = appended
                payload = {
                    **json.loads(draft_json),
                    "format_version": CHECKPOINT_FORMAT_VERSION,
                    "session_id": self._session_id,
                    "trace_head_sha256": trace_head,
                    "trace_sequence": sequence,
                }
                payload_json = canonical_json(payload)
                if len(payload_json.encode("utf-8")) > self._checkpoint_limits.max_payload_bytes:
                    raise _limit_exceeded()
                digest = payload_sha256(payload_json)
                connection.execute(
                    """
                    INSERT INTO checkpoints (
                        checkpoint_id, session_id, source_run_id, trace_sequence,
                        trace_head_sha256, format_version, created_at, payload_json,
                        payload_sha256, status, resumed_run_id, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', NULL, NULL)
                    """,
                    (
                        draft.checkpoint_id,
                        self._session_id,
                        draft.source_run_id,
                        sequence,
                        trace_head,
                        CHECKPOINT_FORMAT_VERSION,
                        draft.created_at.isoformat(),
                        payload_json,
                        digest,
                    ),
                )
                row = _select_checkpoint(connection, self._session_id, draft.checkpoint_id)
                if row is None:
                    raise _corrupt()
                saved = checkpoint_from_row(row)
                connection.commit()
                return saved
            except PersistenceError:
                connection.rollback()
                raise
            except (sqlite3.Error, TypeError, ValueError, ValidationError):
                connection.rollback()
                raise PersistenceError(
                    PersistenceErrorCode.STORAGE_FAILED,
                    "Checkpoint could not be persisted.",
                ) from None


def checkpoint_from_row(row: sqlite3.Row) -> CheckpointSnapshot:
    try:
        payload_json = str(row["payload_json"])
        digest = str(row["payload_sha256"])
        if payload_sha256(payload_json) != digest:
            raise _corrupt()
        raw = json.loads(payload_json)
        if not isinstance(raw, dict):
            raise _corrupt()
        payload = cast(dict[str, object], raw)
        payload.update(
            {
                "payload_sha256": digest,
                "status": str(row["status"]),
                "resumed_run_id": row["resumed_run_id"],
                "consumed_at": row["consumed_at"],
            }
        )
        snapshot = CheckpointSnapshot.model_validate(payload)
        if (
            snapshot.checkpoint_id != str(row["checkpoint_id"])
            or snapshot.session_id != str(row["session_id"])
            or snapshot.source_run_id != str(row["source_run_id"])
            or snapshot.trace_sequence != int(row["trace_sequence"])
            or snapshot.trace_head_sha256 != str(row["trace_head_sha256"])
            or snapshot.format_version != int(row["format_version"])
            or snapshot.created_at != datetime.fromisoformat(str(row["created_at"]))
        ):
            raise _corrupt()
        return snapshot
    except PersistenceError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        raise _corrupt() from None


def snapshot_to_draft(snapshot: CheckpointSnapshot) -> CheckpointDraft:
    return CheckpointDraft(
        checkpoint_id=snapshot.checkpoint_id,
        source_run_id=snapshot.source_run_id,
        created_at=snapshot.created_at,
        system_prompt=snapshot.system_prompt,
        messages=snapshot.messages,
        turns=snapshot.turns,
        tool_calls=snapshot.tool_calls,
        usage=snapshot.usage,
        seen_call_ids=snapshot.seen_call_ids,
        tool_contract_sha256=snapshot.tool_contract_sha256,
        workspace_sha256=snapshot.workspace_sha256,
    )


def _select_checkpoint(
    connection: sqlite3.Connection,
    session_id: str,
    checkpoint_id: str,
) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        connection.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? AND checkpoint_id = ?",
            (session_id, checkpoint_id),
        ).fetchone(),
    )


def _limit_exceeded() -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.LIMIT_EXCEEDED,
        "Checkpoint exceeds the configured limit.",
    )


def _corrupt() -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.TRACE_CORRUPT,
        "Checkpoint integrity check failed.",
    )
