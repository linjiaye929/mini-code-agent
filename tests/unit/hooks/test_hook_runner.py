from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest
from pydantic import JsonValue

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.hooks.models import (
    HookDecision,
    HookOutcome,
    HookPhase,
    HookSource,
    PostToolHookContext,
    PreToolHookResult,
    ToolHookContext,
)
from mini_code_agent.hooks.runner import (
    HookRegistration,
    RecordingHookAuditSink,
    ToolHookRunner,
)
from mini_code_agent.policy.models import (
    ActionPreview,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition


def context(arguments: Mapping[str, JsonValue] | None = None) -> ToolHookContext:
    call = ToolCall(id="call-1", name="write_file", arguments=arguments or {"secret": "value"})
    definition = ToolDefinition(
        name="write_file",
        description="Write a file.",
        input_schema={"type": "object"},
        side_effect=SideEffect.WRITE,
    )
    return ToolHookContext(
        call=call,
        definition=definition,
        preview=ActionPreview(
            tool_call_id=call.id,
            tool_name=call.name,
            side_effect=SideEffect.WRITE,
            risk=RiskLevel.HIGH,
            summary="Write a file.",
        ),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )


class PreHook:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        decision: HookDecision = HookDecision.CONTINUE,
        error: BaseException | None = None,
        delay: float = 0,
        invalid: bool = False,
    ) -> None:
        self.name = name
        self.calls = calls
        self.decision = decision
        self.error = error
        self.delay = delay
        self.invalid = invalid

    async def before_tool(self, context: ToolHookContext) -> PreToolHookResult:
        del context
        self.calls.append(self.name)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.invalid:
            return "invalid"  # type: ignore[return-value]
        return PreToolHookResult(
            decision=self.decision,
            public_reason=f"{self.name} decision.",
        )


class PostHook:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        error: BaseException | None = None,
        invalid: bool = False,
    ) -> None:
        self.name = name
        self.calls = calls
        self.error = error
        self.invalid = invalid

    async def after_tool(self, context: PostToolHookContext) -> None:
        del context
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error
        if self.invalid:
            return "invalid"  # type: ignore[return-value]


def registration(
    hook_id: str,
    handler: object,
    *,
    phase: HookPhase = HookPhase.PRE_TOOL,
    priority: int = 0,
) -> HookRegistration:
    return HookRegistration(
        hook_id=hook_id,
        source=HookSource.MANAGED,
        priority=priority,
        phase=phase,
        handler=handler,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_pre_hooks_run_by_priority_then_id_and_stop_on_block() -> None:
    calls: list[str] = []
    audit = RecordingHookAuditSink()
    runner = ToolHookRunner(
        (
            registration("z-last", PreHook("z-last", calls), priority=10),
            registration(
                "b-block",
                PreHook("b-block", calls, decision=HookDecision.BLOCK),
            ),
            registration("a-first", PreHook("a-first", calls)),
        ),
        audit=audit,
    )

    result = await runner.before_tool(context())

    assert calls == ["a-first", "b-block"]
    assert result.allowed is False
    assert result.hook_id == "b-block"
    assert [record.outcome for record in audit.records] == [
        HookOutcome.CONTINUED,
        HookOutcome.BLOCKED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hook", "failure_code", "outcome"),
    [
        (
            PreHook("raises", [], error=RuntimeError("secret-error")),
            "hook_failed",
            HookOutcome.FAILED,
        ),
        (PreHook("invalid", [], invalid=True), "invalid_hook_result", HookOutcome.FAILED),
        (PreHook("slow", [], delay=0.05), "hook_timeout", HookOutcome.TIMED_OUT),
    ],
)
async def test_pre_hook_failures_block_with_static_audit(
    hook: PreHook,
    failure_code: str,
    outcome: HookOutcome,
) -> None:
    audit = RecordingHookAuditSink()
    runner = ToolHookRunner(
        (registration("test-hook", hook),),
        timeout_seconds=0.01,
        audit=audit,
    )

    result = await runner.before_tool(context({"secret": "do-not-audit"}))

    assert result.allowed is False
    assert result.failure_code == failure_code
    assert audit.records[0].outcome is outcome
    serialized = audit.records[0].model_dump_json()
    assert "secret-error" not in serialized
    assert "do-not-audit" not in serialized


class FailingAudit:
    def publish(self, record: object) -> None:
        del record
        raise RuntimeError("secret-audit-error")


@pytest.mark.asyncio
async def test_pre_audit_failure_blocks() -> None:
    runner = ToolHookRunner(
        (registration("audited", PreHook("audited", [])),),
        audit=FailingAudit(),  # type: ignore[arg-type]
    )
    result = await runner.before_tool(context())
    assert result.allowed is False
    assert result.failure_code == "hook_audit_failed"


@pytest.mark.asyncio
async def test_post_failures_are_isolated_and_later_hooks_continue() -> None:
    calls: list[str] = []
    audit = RecordingHookAuditSink()
    runner = ToolHookRunner(
        (
            registration(
                "a-fails",
                PostHook("a-fails", calls, error=RuntimeError("secret")),
                phase=HookPhase.POST_TOOL,
            ),
            registration(
                "b-invalid",
                PostHook("b-invalid", calls, invalid=True),
                phase=HookPhase.POST_TOOL,
            ),
            registration(
                "c-completes",
                PostHook("c-completes", calls),
                phase=HookPhase.POST_TOOL,
            ),
        ),
        audit=audit,
    )
    before = context()
    await runner.after_tool(
        PostToolHookContext(
            **before.model_dump(),
            result=ToolResult(tool_call_id="call-1", content='{"secret":"result"}'),
        )
    )

    assert calls == ["a-fails", "b-invalid", "c-completes"]
    assert [record.outcome for record in audit.records] == [
        HookOutcome.FAILED,
        HookOutcome.FAILED,
        HookOutcome.COMPLETED,
    ]
    assert '"secret"' not in "".join(record.model_dump_json() for record in audit.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [HookPhase.PRE_TOOL, HookPhase.POST_TOOL])
async def test_cancellation_propagates(phase: HookPhase) -> None:
    calls: list[str] = []
    if phase is HookPhase.PRE_TOOL:
        runner = ToolHookRunner(
            (registration("cancel", PreHook("cancel", calls, error=asyncio.CancelledError())),)
        )
        with pytest.raises(asyncio.CancelledError):
            await runner.before_tool(context())
    else:
        runner = ToolHookRunner(
            (
                registration(
                    "cancel",
                    PostHook("cancel", calls, error=asyncio.CancelledError()),
                    phase=HookPhase.POST_TOOL,
                ),
            )
        )
        before = context()
        with pytest.raises(asyncio.CancelledError):
            await runner.after_tool(
                PostToolHookContext(
                    **before.model_dump(),
                    result=ToolResult(tool_call_id="call-1", content="done"),
                )
            )


def test_runner_rejects_duplicate_invalid_or_excess_registrations() -> None:
    hook = PreHook("hook", [])
    duplicate = registration("same", hook)
    with pytest.raises(ValueError):
        ToolHookRunner((duplicate, duplicate))
    with pytest.raises(ValueError):
        HookRegistration(
            hook_id="Invalid ID",
            source=HookSource.MANAGED,
            priority=0,
            phase=HookPhase.PRE_TOOL,
            handler=hook,
        )
    with pytest.raises(ValueError):
        ToolHookRunner(tuple(registration(f"hook-{index}", hook) for index in range(65)))
