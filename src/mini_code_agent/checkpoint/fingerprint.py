from __future__ import annotations

import hashlib
import json

from mini_code_agent.tools.base import ToolDefinition


def tool_contract_sha256(definitions: tuple[ToolDefinition, ...]) -> str:
    names = tuple(definition.name for definition in definitions)
    if len(set(names)) != len(names):
        raise ValueError("Tool definitions must have unique names.")
    payload = [
        definition.model_dump(mode="json")
        for definition in sorted(definitions, key=lambda item: item.name)
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
