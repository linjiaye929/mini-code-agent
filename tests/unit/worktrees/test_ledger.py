from __future__ import annotations

import json

import pytest

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.workspace.models import MutationResult
from mini_code_agent.worktrees.ledger import (
    LedgerRecordingToolExecutor,
    MutationLedger,
    MutationLedgerError,
)

from .test_child_tools import governed_tools


def call(call_id: str, *, name: str = "write_file") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={"unused": True})


def mutation_result(
    call_id: str,
    *,
    path: str = "src/app.py",
    created: bool = False,
    before: str | None = "a" * 64,
    after: str = "b" * 64,
) -> ToolResult:
    mutation = MutationResult(
        path=path,
        created=created,
        before_sha256=before,
        after_sha256=after,
        byte_count=12,
        line_count=1,
        diff="bounded",
    )
    return ToolResult(
        tool_call_id=call_id,
        content=json.dumps(mutation.model_dump(mode="json")),
    )


def test_ledger_records_ordered_host_result_hash_chain() -> None:
    ledger = MutationLedger(max_entries=4)

    ledger.record(call("call:1"), mutation_result("call:1"))
    ledger.record(
        call("call:2", name="edit_file"),
        mutation_result("call:2", before="b" * 64, after="c" * 64),
    )
    ledger.record(
        call("call:3"),
        mutation_result(
            "call:3",
            path="src/new.py",
            created=True,
            before=None,
            after="d" * 64,
        ),
    )

    assert ledger.compromised is False
    assert [entry.ordinal for entry in ledger.entries] == [0, 1, 2]
    assert ledger.entries[1].before_sha256 == ledger.entries[0].after_sha256
    assert ledger.entries[2].created is True


@pytest.mark.parametrize(
    ("second_call", "second_result"),
    [
        (
            call("call-1"),
            mutation_result("call-1", before="b" * 64, after="c" * 64),
        ),
        (
            call("call-2"),
            mutation_result("call-2", before="f" * 64, after="c" * 64),
        ),
        (
            call("call-2"),
            ToolResult(tool_call_id="call-2", content='{"forged":true}'),
        ),
        (
            call("call-2"),
            mutation_result("different", before="b" * 64, after="c" * 64),
        ),
    ],
)
def test_ledger_fails_closed_on_duplicate_discontinuous_or_malformed_results(
    second_call: ToolCall,
    second_result: ToolResult,
) -> None:
    ledger = MutationLedger(max_entries=4)
    ledger.record(call("call-1"), mutation_result("call-1"))

    with pytest.raises(MutationLedgerError):
        ledger.record(second_call, second_result)

    assert ledger.compromised is True
    with pytest.raises(MutationLedgerError):
        ledger.record(
            call("call-3"),
            mutation_result("call-3", before="b" * 64, after="c" * 64),
        )


def test_ledger_ignores_read_and_failed_mutation_results() -> None:
    ledger = MutationLedger(max_entries=4)

    ledger.record(
        call("read-1", name="read_file"),
        ToolResult(tool_call_id="read-1", content="read"),
    )
    ledger.record(
        call("write-1"),
        ToolResult(tool_call_id="write-1", content='{"error":{}}', is_error=True),
    )

    assert ledger.entries == ()
    assert ledger.compromised is False


@pytest.mark.asyncio
async def test_recording_executor_derives_ledger_only_after_successful_execution() -> None:
    tools = governed_tools()
    ledger = MutationLedger(max_entries=4)
    wrapped = LedgerRecordingToolExecutor(tools, ledger)
    write = call("call-1")
    tools.results["call-1"] = mutation_result("call-1")

    result = await wrapped.execute(write)

    assert result.is_error is False
    assert len(ledger.entries) == 1
    assert wrapped.definitions == tools.definitions
    assert wrapped.governance_enforced is True
    assert wrapped.trust_source_for("write_file") == tools.trust_source_for("write_file")


@pytest.mark.asyncio
async def test_recording_executor_returns_static_error_when_ledger_is_compromised() -> None:
    tools = governed_tools()
    ledger = MutationLedger(max_entries=4)
    wrapped = LedgerRecordingToolExecutor(tools, ledger)
    tools.results["call-1"] = ToolResult(tool_call_id="call-1", content='{"forged":true}')

    result = await wrapped.execute(call("call-1"))

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "mutation_ledger_failed"
    assert ledger.compromised is True
