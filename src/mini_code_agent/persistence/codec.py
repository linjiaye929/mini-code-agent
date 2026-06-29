from __future__ import annotations

import hashlib
import json
from typing import cast

from pydantic import TypeAdapter, ValidationError

from mini_code_agent.agent.events import AgentEvent, RunStopped
from mini_code_agent.persistence.errors import (
    PersistenceError,
    PersistenceErrorCode,
)
from mini_code_agent.persistence.models import TRACE_SCHEMA_VERSION

_EVENT_ADAPTER = TypeAdapter[AgentEvent](AgentEvent)


def encode_event(
    event: AgentEvent,
    secrets: tuple[str, ...],
) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], event.model_dump(mode="json"))
    if isinstance(event, RunStopped) and isinstance(payload.get("error"), str):
        error = cast(str, payload["error"])
        for secret in secrets:
            error = error.replace(secret, "***")
        payload["error"] = error
    return payload, canonical_json(payload)


def decode_event(payload_json: str) -> tuple[dict[str, object], AgentEvent]:
    try:
        raw = json.loads(payload_json)
        if not isinstance(raw, dict):
            raise TypeError
        payload = cast(dict[str, object], raw)
        event = _EVENT_ADAPTER.validate_python(payload)
        return payload, event
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
        raise trace_corrupt() from None


def event_sha256(
    *,
    session_id: str,
    sequence: int,
    previous_sha256: str,
    event_payload: dict[str, object],
) -> str:
    envelope = {
        "event": event_payload,
        "previous_sha256": previous_sha256,
        "schema_version": TRACE_SCHEMA_VERSION,
        "sequence": sequence,
        "session_id": session_id,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def trace_corrupt() -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.TRACE_CORRUPT,
        "Session trace integrity check failed.",
    )
