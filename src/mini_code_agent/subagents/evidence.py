from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

from mini_code_agent.agent.models import AgentResult
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.subagents.models import SubagentEvidenceItem


class SubagentEvidenceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Subagent transcript evidence was invalid.")


def extract_subagent_evidence(
    result: AgentResult,
    *,
    max_items: int,
) -> tuple[SubagentEvidenceItem, ...]:
    if not 0 <= max_items <= 256:
        raise SubagentEvidenceError

    ordered_calls: list[ToolCall] = []
    calls: dict[str, ToolCall] = {}
    results: dict[str, ToolResult] = {}
    for message in result.messages:
        for block in message.content:
            if isinstance(block, ToolCall):
                if block.id in calls or block.id in results:
                    raise SubagentEvidenceError
                calls[block.id] = block
                ordered_calls.append(block)
            elif isinstance(block, ToolResult):
                if block.tool_call_id not in calls or block.tool_call_id in results:
                    raise SubagentEvidenceError
                results[block.tool_call_id] = block

    if (
        len(ordered_calls) != result.tool_calls
        or len(ordered_calls) > max_items
        or len(results) != len(ordered_calls)
    ):
        raise SubagentEvidenceError

    return tuple(_evidence_item(call, results[call.id]) for call in ordered_calls)


def subagent_result_sha256(value: BaseModel) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_item(
    call: ToolCall,
    result: ToolResult,
) -> SubagentEvidenceItem:
    content = result.content.encode("utf-8")
    return SubagentEvidenceItem(
        tool_call_id=call.id,
        tool_name=call.name,
        is_error=result.is_error,
        content_chars=len(result.content),
        content_sha256=hashlib.sha256(content).hexdigest(),
    )
