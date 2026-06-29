from __future__ import annotations

import hashlib
import json
from typing import cast

from mini_code_agent.checkpoint.models import CheckpointDraft
from mini_code_agent.domain.messages import Message


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def encode_draft(draft: CheckpointDraft) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], draft.model_dump(mode="json"))
    payload["seen_call_ids"] = sorted(draft.seen_call_ids)
    return payload, canonical_json(payload)


def transcript_sha256(messages: tuple[Message, ...]) -> str:
    payload = [message.model_dump(mode="json") for message in messages]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def payload_sha256(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
