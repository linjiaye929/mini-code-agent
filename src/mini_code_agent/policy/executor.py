from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Literal, Protocol, cast

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.hooks.models import PostToolHookContext, ToolHookContext
from mini_code_agent.hooks.runner import ToolHookRunner
from mini_code_agent.policy.approval import ApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.models import (
    ActionGuard,
    ActionPreview,
    ApprovalRequest,
    PolicyDecision,
    PolicyRequest,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.tools.registry import RegisteredTool, ToolRegistry


class _PreviewableTool(Protocol):
    async def preview(self, call: ToolCall) -> ActionPreview: ...


class GovernedToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy: PolicyEngine,
        approval: ApprovalHandler,
        session_mode: SessionMode,
        trust_source: TrustSource,
        trust_sources: Mapping[str, TrustSource] | None = None,
        guard: ActionGuard | None = None,
        hooks: ToolHookRunner | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._approval = approval
        self._session_mode = session_mode
        self._trust_source = trust_source
        raw_overrides = dict(cast(Mapping[str, object], trust_sources or {}))
        registered_names = {item.name for item in registry.definitions}
        if not set(raw_overrides).issubset(registered_names):
            raise ValueError("Tool trust sources must reference registered tools.")
        if any(not isinstance(value, TrustSource) for value in raw_overrides.values()):
            raise ValueError("Tool trust sources must be TrustSource values.")
        self._trust_sources = {
            key: cast(TrustSource, value) for key, value in raw_overrides.items()
        }
        self._guard = guard
        self._hooks = hooks

    @property
    def governance_enforced(self) -> Literal[True]:
        return True

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._registry.definitions

    def trust_source_for(self, tool_name: str) -> TrustSource:
        return self._trust_sources.get(tool_name, self._trust_source)

    async def execute(self, call: ToolCall) -> ToolResult:
        validation_error = self._registry.validate(call)
        if validation_error is not None:
            return validation_error
        definition = self._registry.definition_for(call.name)
        tool = self._registry.tool_for(call.name)
        if definition is None or tool is None:
            return self._error(
                call.id,
                "unknown_tool",
                "The requested tool is not registered.",
            )
        trust_source = self.trust_source_for(call.name)

        preview = await self._preview(call, definition, tool)
        if preview is None:
            return self._error(
                call.id,
                "preview_failed",
                "Tool action preview could not be created.",
            )
        if self._guard is not None:
            try:
                guard_result = self._guard.evaluate(preview)
            except Exception:
                return self._permission_denied(call.id)
            if not guard_result.allowed:
                return self._permission_denied(call.id)
        hook_context: ToolHookContext | None = None
        if self._hooks is not None:
            hook_context = ToolHookContext(
                call=call,
                definition=definition,
                preview=preview,
                session_mode=self._session_mode,
                trust_source=trust_source,
            )
            hook_result = await self._hooks.before_tool(hook_context)
            if not hook_result.allowed:
                return self._permission_denied(call.id)
        policy_result = self._policy.evaluate(
            PolicyRequest(
                tool_name=call.name,
                side_effect=definition.side_effect,
                risk=preview.risk,
                resources=preview.resources,
                command=preview.command or (),
                session_mode=self._session_mode,
                trust_source=trust_source,
            )
        )
        if policy_result.decision is PolicyDecision.DENY:
            return self._permission_denied(call.id)
        if policy_result.decision is PolicyDecision.ASK:
            if self._session_mode is SessionMode.NON_INTERACTIVE:
                return self._permission_denied(call.id)
            try:
                approved = await self._approval.approve(
                    ApprovalRequest(
                        preview=preview,
                        rule_id=policy_result.rule_id,
                        rationale=policy_result.rationale,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return self._error(
                    call.id,
                    "approval_failed",
                    "Tool approval failed.",
                )
            if not approved:
                return self._permission_denied(call.id)
        result = await self._registry.execute(call)
        if self._hooks is not None and hook_context is not None:
            await self._hooks.after_tool(
                PostToolHookContext(
                    **hook_context.model_dump(),
                    result=result,
                )
            )
        return result

    async def _preview(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        tool: RegisteredTool,
    ) -> ActionPreview | None:
        preview_method = getattr(tool, "preview", None)
        if preview_method is None:
            if definition.side_effect is not SideEffect.READ_ONLY:
                return None
            return ActionPreview(
                tool_call_id=call.id,
                tool_name=call.name,
                side_effect=definition.side_effect,
                risk=RiskLevel.LOW,
                summary=f"Run read-only tool {call.name}.",
            )
        try:
            candidate = cast(
                object,
                await cast(_PreviewableTool, tool).preview(call),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if not isinstance(candidate, ActionPreview):
            return None
        if (
            candidate.tool_call_id != call.id
            or candidate.tool_name != call.name
            or candidate.side_effect is not definition.side_effect
        ):
            return None
        return candidate

    @classmethod
    def _permission_denied(cls, call_id: str) -> ToolResult:
        return cls._error(
            call_id,
            "permission_denied",
            "Tool execution was not permitted.",
        )

    @staticmethod
    def _error(call_id: str, code: str, message: str) -> ToolResult:
        return ToolResult(
            tool_call_id=call_id,
            content=json.dumps(
                {"error": {"code": code, "message": message}},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            is_error=True,
        )
