from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest
from pydantic import JsonValue

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.hooks.models import (
    HookDecision,
    HookPhase,
    HookSource,
    PostToolHookContext,
    PreToolHookResult,
    ToolHookContext,
)
from mini_code_agent.hooks.runner import HookRegistration, ToolHookRunner
from mini_code_agent.policy.approval import (
    ApprovalHandler,
    DenyAllApprovalHandler,
    StaticApprovalHandler,
)
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import (
    ActionGuardResult,
    ActionPreview,
    PolicyDecision,
    PolicyRule,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.tools.registry import ToolRegistry


class RecordingTool:
    def __init__(
        self,
        *,
        name: str,
        side_effect: SideEffect,
        schema: Mapping[str, JsonValue] | None = None,
        preview: object | None = None,
        preview_error: Exception | None = None,
    ) -> None:
        self._definition = ToolDefinition(
            name=name,
            description="Test governed tool.",
            input_schema=schema
            or {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect=side_effect,
        )
        self._preview = preview
        self._preview_error = preview_error
        self.calls: list[ToolCall] = []
        self.preview_calls: list[ToolCall] = []

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        self.preview_calls.append(call)
        if self._preview_error is not None:
            raise self._preview_error
        if self._preview is not None:
            return self._preview  # type: ignore[return-value]
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=call.name,
            side_effect=self._definition.side_effect,
            risk=(
                RiskLevel.LOW
                if self._definition.side_effect is SideEffect.READ_ONLY
                else RiskLevel.HIGH
            ),
            summary="Access src/app.py.",
            resources=("src/app.py",),
            diff="--- before\n+++ after\n",
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult(tool_call_id=call.id, content='{"ok":true}')


def call(
    *,
    name: str = "write_file",
    arguments: Mapping[str, JsonValue] | None = None,
) -> ToolCall:
    return ToolCall(
        id="call-1",
        name=name,
        arguments={"path": "src/app.py"} if arguments is None else arguments,
    )


def error_code(result: ToolResult) -> str:
    payload = json.loads(result.content)
    return payload["error"]["code"]  # type: ignore[no-any-return]


def executor_for(
    tool: RecordingTool,
    *,
    policy: PolicyEngine | None = None,
    approval: ApprovalHandler | None = None,
    session_mode: SessionMode = SessionMode.INTERACTIVE,
    guard: object | None = None,
) -> GovernedToolExecutor:
    return GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=policy or PolicyEngine(),
        approval=approval or DenyAllApprovalHandler(),
        session_mode=session_mode,
        trust_source=TrustSource.MODEL,
        guard=guard,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_invalid_arguments_return_before_preview_policy_or_approval() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    approval = StaticApprovalHandler(approved=True)
    executor = executor_for(tool, approval=approval)

    result = await executor.execute(call(arguments={}))

    assert error_code(result) == "invalid_arguments"
    assert tool.preview_calls == []
    assert tool.calls == []
    assert approval.requests == []


@pytest.mark.asyncio
async def test_default_read_only_policy_dispatches_without_approval() -> None:
    tool = RecordingTool(name="read_test", side_effect=SideEffect.READ_ONLY)
    approval = StaticApprovalHandler(approved=False)
    executor = executor_for(tool, approval=approval)

    result = await executor.execute(call(name="read_test"))

    assert result.is_error is False
    assert tool.calls == [call(name="read_test")]
    assert approval.requests == []


@pytest.mark.asyncio
async def test_deny_never_dispatches() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    policy = PolicyEngine(
        rules=(
            PolicyRule(
                id="deny-write",
                decision=PolicyDecision.DENY,
                rationale="Writes disabled.",
                side_effect=SideEffect.WRITE,
            ),
        )
    )
    executor = executor_for(
        tool,
        policy=policy,
        approval=StaticApprovalHandler(approved=True),
    )

    result = await executor.execute(call())

    assert error_code(result) == "permission_denied"
    assert tool.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_ask_dispatches_only_after_explicit_approval(approved: bool) -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    approval = StaticApprovalHandler(approved=approved)
    executor = executor_for(tool, approval=approval)

    result = await executor.execute(call())

    assert len(approval.requests) == 1
    request = approval.requests[0]
    assert request.preview.resources == ("src/app.py",)
    assert request.preview.command is None
    assert request.preview.diff == "--- before\n+++ after\n"
    assert request.rule_id == "default-write"
    assert bool(tool.calls) is approved
    assert result.is_error is (not approved)


@pytest.mark.asyncio
async def test_non_interactive_ask_is_denied() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    approval = StaticApprovalHandler(approved=True)
    executor = executor_for(
        tool,
        approval=approval,
        session_mode=SessionMode.NON_INTERACTIVE,
    )

    result = await executor.execute(call())

    assert error_code(result) == "permission_denied"
    assert tool.calls == []
    assert approval.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preview", "preview_error"),
    [
        ("not-a-preview", None),
        (
            ActionPreview(
                tool_call_id="different",
                tool_name="write_file",
                side_effect=SideEffect.WRITE,
                risk=RiskLevel.HIGH,
                summary="Wrong ID.",
                resources=("src/app.py",),
            ),
            None,
        ),
        (None, RuntimeError("secret-preview-error")),
    ],
)
async def test_invalid_preview_fails_closed(
    preview: object | None,
    preview_error: Exception | None,
) -> None:
    tool = RecordingTool(
        name="write_file",
        side_effect=SideEffect.WRITE,
        preview=preview,
        preview_error=preview_error,
    )
    executor = executor_for(
        tool,
        approval=StaticApprovalHandler(approved=True),
    )

    result = await executor.execute(call())

    assert error_code(result) == "preview_failed"
    assert tool.calls == []
    assert "secret-preview-error" not in result.content


class RaisingApproval:
    async def approve(self, request: object) -> bool:
        del request
        raise RuntimeError("secret-approval-error")


class CancellingApproval:
    async def approve(self, request: object) -> bool:
        del request
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_approval_exception_fails_closed_without_leak() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    executor = executor_for(tool, approval=RaisingApproval())

    result = await executor.execute(call())

    assert error_code(result) == "approval_failed"
    assert "secret-approval-error" not in result.content
    assert tool.calls == []


@pytest.mark.asyncio
async def test_approval_cancellation_is_propagated() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    executor = executor_for(tool, approval=CancellingApproval())

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(call())

    assert tool.calls == []


def test_governed_executor_exposes_immutable_definitions_and_marker() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    executor = executor_for(tool)

    assert executor.governance_enforced is True
    assert executor.definitions == (tool.definition,)


class RecordingGuard:
    def __init__(
        self,
        result: ActionGuardResult | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result or ActionGuardResult(allowed=True)
        self.error = error
        self.previews: list[ActionPreview] = []

    def evaluate(self, preview: ActionPreview) -> ActionGuardResult:
        self.previews.append(preview)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_action_guard_denies_before_policy_approval_and_execution() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    approval = StaticApprovalHandler(approved=True)
    guard = RecordingGuard(
        ActionGuardResult(
            allowed=False,
            public_message="Repair scope denied the action.",
        )
    )
    executor = executor_for(tool, approval=approval, guard=guard)

    result = await executor.execute(call())

    assert error_code(result) == "permission_denied"
    assert len(guard.previews) == 1
    assert approval.requests == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_action_guard_exception_fails_closed_without_leaking() -> None:
    tool = RecordingTool(name="read_test", side_effect=SideEffect.READ_ONLY)
    guard = RecordingGuard(error=RuntimeError("secret-guard-error"))
    executor = executor_for(tool, guard=guard)

    result = await executor.execute(call(name="read_test"))

    assert error_code(result) == "permission_denied"
    assert "secret-guard-error" not in result.content
    assert tool.calls == []


@pytest.mark.asyncio
async def test_omitted_action_guard_preserves_existing_behavior() -> None:
    tool = RecordingTool(name="read_test", side_effect=SideEffect.READ_ONLY)
    executor = executor_for(tool)

    result = await executor.execute(call(name="read_test"))

    assert result.is_error is False
    assert tool.calls == [call(name="read_test")]


class StaticPreHook:
    def __init__(
        self,
        decision: HookDecision,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.decision = decision
        self.error = error
        self.contexts: list[ToolHookContext] = []

    async def before_tool(self, context: ToolHookContext) -> PreToolHookResult:
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return PreToolHookResult(decision=self.decision)


class RecordingPostHook:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.contexts: list[PostToolHookContext] = []

    async def after_tool(self, context: PostToolHookContext) -> None:
        self.contexts.append(context)
        if self.error is not None:
            raise self.error


def hook_runner(
    *,
    pre: StaticPreHook | None = None,
    post: RecordingPostHook | None = None,
) -> ToolHookRunner:
    registrations: list[HookRegistration] = []
    if pre is not None:
        registrations.append(
            HookRegistration(
                hook_id="pre-check",
                source=HookSource.MANAGED,
                priority=0,
                phase=HookPhase.PRE_TOOL,
                handler=pre,
            )
        )
    if post is not None:
        registrations.append(
            HookRegistration(
                hook_id="post-observer",
                source=HookSource.MANAGED,
                priority=0,
                phase=HookPhase.POST_TOOL,
                handler=post,
            )
        )
    return ToolHookRunner(registrations)


@pytest.mark.asyncio
async def test_pre_hook_block_happens_before_policy_approval_and_execution() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    approval = StaticApprovalHandler(approved=True)
    pre = StaticPreHook(HookDecision.BLOCK)
    executor = GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=PolicyEngine(),
        approval=approval,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        hooks=hook_runner(pre=pre),
    )

    result = await executor.execute(call())

    assert error_code(result) == "permission_denied"
    assert len(pre.contexts) == 1
    assert approval.requests == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_hook_continue_cannot_bypass_policy_deny() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    pre = StaticPreHook(HookDecision.CONTINUE)
    post = RecordingPostHook()
    executor = GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=PolicyEngine(
            rules=(
                PolicyRule(
                    id="deny-write",
                    decision=PolicyDecision.DENY,
                    rationale="Writes disabled.",
                    side_effect=SideEffect.WRITE,
                ),
            )
        ),
        approval=StaticApprovalHandler(approved=True),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        hooks=hook_runner(pre=pre, post=post),
    )

    result = await executor.execute(call())

    assert error_code(result) == "permission_denied"
    assert len(pre.contexts) == 1
    assert post.contexts == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_post_hook_observes_execution_without_replacing_result() -> None:
    tool = RecordingTool(name="read_test", side_effect=SideEffect.READ_ONLY)
    post = RecordingPostHook(error=RuntimeError("observer-secret"))
    executor = GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=PolicyEngine(),
        approval=DenyAllApprovalHandler(),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        hooks=hook_runner(post=post),
    )

    result = await executor.execute(call(name="read_test"))

    assert result == ToolResult(tool_call_id="call-1", content='{"ok":true}')
    assert len(post.contexts) == 1
    assert post.contexts[0].result is result
    assert tool.calls == [call(name="read_test")]


@pytest.mark.asyncio
async def test_hook_cancellation_propagates_without_tool_execution() -> None:
    tool = RecordingTool(name="read_test", side_effect=SideEffect.READ_ONLY)
    pre = StaticPreHook(HookDecision.CONTINUE, error=asyncio.CancelledError())
    executor = GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=PolicyEngine(),
        approval=DenyAllApprovalHandler(),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        hooks=hook_runner(pre=pre),
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(call(name="read_test"))

    assert tool.calls == []


@pytest.mark.asyncio
async def test_per_tool_trust_source_reaches_hooks_and_policy() -> None:
    tool = RecordingTool(name="mcp_status", side_effect=SideEffect.READ_ONLY)
    pre = StaticPreHook(HookDecision.CONTINUE)
    executor = GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=PolicyEngine(
            rules=(
                PolicyRule(
                    id="deny-extension",
                    decision=PolicyDecision.DENY,
                    rationale="Extension tools disabled.",
                    trust_source=TrustSource.EXTENSION,
                ),
            )
        ),
        approval=DenyAllApprovalHandler(),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        trust_sources={"mcp_status": TrustSource.EXTENSION},
        hooks=hook_runner(pre=pre),
    )

    result = await executor.execute(call(name="mcp_status"))

    assert error_code(result) == "permission_denied"
    assert pre.contexts[0].trust_source is TrustSource.EXTENSION
    assert tool.calls == []


@pytest.mark.asyncio
async def test_default_trust_source_is_preserved_without_override() -> None:
    tool = RecordingTool(name="mcp_status", side_effect=SideEffect.READ_ONLY)
    executor = GovernedToolExecutor(
        ToolRegistry([tool]),
        policy=PolicyEngine(
            rules=(
                PolicyRule(
                    id="deny-extension",
                    decision=PolicyDecision.DENY,
                    rationale="Extension tools disabled.",
                    trust_source=TrustSource.EXTENSION,
                ),
            )
        ),
        approval=DenyAllApprovalHandler(),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )

    result = await executor.execute(call(name="mcp_status"))

    assert result.is_error is False
    assert tool.calls == [call(name="mcp_status")]


def test_trust_source_mapping_rejects_unknown_tools_and_is_copied() -> None:
    tool = RecordingTool(name="mcp_status", side_effect=SideEffect.READ_ONLY)
    registry = ToolRegistry([tool])
    with pytest.raises(ValueError, match="registered"):
        GovernedToolExecutor(
            registry,
            policy=PolicyEngine(),
            approval=DenyAllApprovalHandler(),
            session_mode=SessionMode.INTERACTIVE,
            trust_source=TrustSource.MODEL,
            trust_sources={"missing": TrustSource.EXTENSION},
        )

    mapping = {"mcp_status": TrustSource.EXTENSION}
    executor = GovernedToolExecutor(
        registry,
        policy=PolicyEngine(),
        approval=DenyAllApprovalHandler(),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        trust_sources=mapping,
    )
    mapping["mcp_status"] = TrustSource.USER
    assert executor.trust_source_for("mcp_status") is TrustSource.EXTENSION
