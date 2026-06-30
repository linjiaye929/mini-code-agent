from collections.abc import Mapping

import pytest
from pydantic import JsonValue, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.hooks.models import (
    HookAuditRecord,
    HookDecision,
    HookOutcome,
    HookPhase,
    HookSource,
    PostToolHookContext,
    PreToolHookResult,
    ToolHookContext,
)
from mini_code_agent.policy.models import (
    ActionPreview,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition


def context(arguments: Mapping[str, JsonValue] | None = None) -> ToolHookContext:
    call = ToolCall(
        id="call-1",
        name="write_file",
        arguments=arguments or {"path": "secret.txt"},
    )
    definition = ToolDefinition(
        name="write_file",
        description="Write one file.",
        input_schema={"type": "object"},
        side_effect=SideEffect.WRITE,
    )
    preview = ActionPreview(
        tool_call_id=call.id,
        tool_name=call.name,
        side_effect=SideEffect.WRITE,
        risk=RiskLevel.HIGH,
        summary="Write secret.txt.",
        resources=("secret.txt",),
    )
    return ToolHookContext(
        call=call,
        definition=definition,
        preview=preview,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )


def test_hook_context_requires_correlated_tool_identity() -> None:
    valid = context()
    assert valid.preview.tool_call_id == valid.call.id

    with pytest.raises(ValidationError):
        ToolHookContext(
            call=valid.call,
            definition=valid.definition.model_copy(update={"name": "read_file"}),
            preview=valid.preview,
            session_mode=valid.session_mode,
            trust_source=valid.trust_source,
        )


def test_post_context_carries_the_exact_tool_result() -> None:
    before = context()
    result = ToolResult(tool_call_id="call-1", content='{"ok":true}')
    after = PostToolHookContext(**before.model_dump(), result=result)
    assert after.result == result

    with pytest.raises(ValidationError):
        PostToolHookContext(
            **before.model_dump(),
            result=ToolResult(tool_call_id="different", content='{"ok":true}'),
        )


def test_pre_result_is_bounded_and_frozen() -> None:
    result = PreToolHookResult(
        decision=HookDecision.BLOCK,
        public_reason="Protected branch.",
    )
    assert result.decision is HookDecision.BLOCK
    with pytest.raises(ValidationError):
        PreToolHookResult(decision=HookDecision.BLOCK, public_reason="x" * 501)


def test_audit_record_contains_only_bounded_metadata() -> None:
    record = HookAuditRecord(
        hook_id="protect-main",
        source=HookSource.MANAGED,
        phase=HookPhase.PRE_TOOL,
        outcome=HookOutcome.CONTINUED,
        tool_call_id="call-1",
        tool_name="write_file",
        elapsed_ms=3,
    )
    payload = record.model_dump(mode="json")
    assert set(payload) == {
        "hook_id",
        "source",
        "phase",
        "outcome",
        "tool_call_id",
        "tool_name",
        "elapsed_ms",
        "failure_code",
    }
    assert "secret.txt" not in record.model_dump_json()


@pytest.mark.parametrize("elapsed_ms", [-1, 30_001])
def test_audit_elapsed_time_is_bounded(elapsed_ms: int) -> None:
    with pytest.raises(ValidationError):
        HookAuditRecord(
            hook_id="protect-main",
            source=HookSource.MANAGED,
            phase=HookPhase.PRE_TOOL,
            outcome=HookOutcome.FAILED,
            tool_call_id="call-1",
            tool_name="write_file",
            elapsed_ms=elapsed_ms,
            failure_code="hook_failed",
        )
