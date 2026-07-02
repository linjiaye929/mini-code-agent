from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import TrustSource
from mini_code_agent.tools.base import ToolDefinition, ToolExecutor
from mini_code_agent.workspace.models import MutationResult
from mini_code_agent.worktrees.models import MutationLedgerEntry

_MUTATION_TOOLS = frozenset({"write_file", "edit_file"})
_MAX_MUTATION_RESULT_CHARS = 2 * 1024 * 1024


class MutationLedgerError(RuntimeError):
    pass


class MutationLedger:
    def __init__(self, *, max_entries: int = 128) -> None:
        if not 1 <= max_entries <= 128:
            raise ValueError("Mutation ledger entry limit is invalid.")
        self._max_entries = max_entries
        self._entries: list[MutationLedgerEntry] = []
        self._call_ids: set[str] = set()
        self._last_by_path: dict[str, MutationLedgerEntry] = {}
        self._compromised = False

    @property
    def entries(self) -> tuple[MutationLedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def compromised(self) -> bool:
        return self._compromised

    def record(self, call: ToolCall, result: ToolResult) -> None:
        if self._compromised:
            raise MutationLedgerError("Mutation ledger is compromised.")
        if call.name not in _MUTATION_TOOLS or result.is_error:
            return
        try:
            self._record_success(call, result)
        except MutationLedgerError:
            self._compromised = True
            raise
        except Exception:
            self._compromised = True
            raise MutationLedgerError("Mutation evidence was invalid.") from None

    def _record_success(self, call: ToolCall, result: ToolResult) -> None:
        if (
            result.tool_call_id != call.id
            or call.id in self._call_ids
            or len(self._entries) >= self._max_entries
            or len(result.content) > _MAX_MUTATION_RESULT_CHARS
        ):
            raise MutationLedgerError("Mutation evidence identity was invalid.")
        try:
            raw = json.loads(result.content)
            mutation = MutationResult.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            raise MutationLedgerError("Mutation result was malformed.") from None
        previous = self._last_by_path.get(mutation.path)
        if previous is not None and (
            mutation.created or mutation.before_sha256 != previous.after_sha256
        ):
            raise MutationLedgerError("Mutation hash chain was discontinuous.")
        entry = MutationLedgerEntry(
            ordinal=len(self._entries),
            tool_call_id=call.id,
            tool_name=cast(Literal["write_file", "edit_file"], call.name),
            path=mutation.path,
            created=mutation.created,
            before_sha256=mutation.before_sha256,
            after_sha256=mutation.after_sha256,
            byte_count=mutation.byte_count,
            line_count=mutation.line_count,
        )
        self._entries.append(entry)
        self._call_ids.add(call.id)
        self._last_by_path[entry.path] = entry


class LedgerRecordingToolExecutor:
    def __init__(self, tools: ToolExecutor, ledger: MutationLedger) -> None:
        self._tools = tools
        self._ledger = ledger

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._tools.definitions

    @property
    def governance_enforced(self) -> Literal[True]:
        if getattr(self._tools, "governance_enforced", None) is not True:
            raise ValueError("Wrapped Tool executor is not governed.")
        return True

    def trust_source_for(self, tool_name: str) -> TrustSource:
        resolver = getattr(self._tools, "trust_source_for", None)
        if not callable(resolver):
            raise ValueError("Wrapped Tool executor has no trust source.")
        candidate = resolver(tool_name)
        if not isinstance(candidate, TrustSource):
            raise ValueError("Wrapped Tool trust source is invalid.")
        return candidate

    async def execute(self, call: ToolCall) -> ToolResult:
        if self._ledger.compromised and call.name in _MUTATION_TOOLS:
            return _ledger_error(call.id)
        result = await self._tools.execute(call)
        try:
            self._ledger.record(call, result)
        except MutationLedgerError:
            return _ledger_error(call.id)
        return result


def _ledger_error(call_id: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=json.dumps(
            {
                "error": {
                    "code": "mutation_ledger_failed",
                    "message": "Mutation evidence could not be recorded safely.",
                }
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        is_error=True,
    )
