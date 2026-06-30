from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, cast

from mini_code_agent.hooks.models import (
    HookAuditRecord,
    HookDecision,
    HookGateResult,
    HookOutcome,
    HookPhase,
    HookSource,
    PostToolHookContext,
    PreToolHookResult,
    ToolHookContext,
)

_HOOK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class PreToolHook(Protocol):
    async def before_tool(self, context: ToolHookContext) -> PreToolHookResult: ...


class PostToolHook(Protocol):
    async def after_tool(self, context: PostToolHookContext) -> None: ...


class HookAuditSink(Protocol):
    def publish(self, record: HookAuditRecord) -> None: ...


class NullHookAuditSink:
    def publish(self, record: HookAuditRecord) -> None:
        del record


class RecordingHookAuditSink:
    def __init__(self) -> None:
        self.records: list[HookAuditRecord] = []

    def publish(self, record: HookAuditRecord) -> None:
        self.records.append(record)


@dataclass(frozen=True, slots=True)
class HookRegistration:
    hook_id: str
    source: HookSource
    priority: int
    phase: HookPhase
    handler: PreToolHook | PostToolHook

    def __post_init__(self) -> None:
        if _HOOK_ID.fullmatch(self.hook_id) is None:
            raise ValueError("Hook ID is invalid.")
        if not -1000 <= self.priority <= 1000:
            raise ValueError("Hook priority must be between -1000 and 1000.")
        method_name = "before_tool" if self.phase is HookPhase.PRE_TOOL else "after_tool"
        if not callable(getattr(self.handler, method_name, None)):
            raise ValueError("Hook handler does not implement its registered phase.")


class ToolHookRunner:
    def __init__(
        self,
        registrations: Iterable[HookRegistration] = (),
        *,
        timeout_seconds: float = 1.0,
        audit: HookAuditSink | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        ordered = tuple(
            sorted(
                registrations,
                key=lambda item: (item.phase.value, item.priority, item.hook_id),
            )
        )
        if len(ordered) > 64:
            raise ValueError("At most 64 Hooks may be registered.")
        identities = tuple(item.hook_id for item in ordered)
        if len(identities) != len(set(identities)):
            raise ValueError("Hook IDs must be unique across phases.")
        if not 0.01 <= timeout_seconds <= 30:
            raise ValueError("Hook timeout must be between 0.01 and 30 seconds.")
        self._pre = tuple(item for item in ordered if item.phase is HookPhase.PRE_TOOL)
        self._post = tuple(item for item in ordered if item.phase is HookPhase.POST_TOOL)
        self._timeout_seconds = timeout_seconds
        self._audit = audit or NullHookAuditSink()
        self._monotonic_ns = monotonic_ns

    async def before_tool(self, context: ToolHookContext) -> HookGateResult:
        for registration in self._pre:
            started = self._monotonic_ns()
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    candidate = cast(
                        object,
                        await cast(PreToolHook, registration.handler).before_tool(context),
                    )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                record = self._record(
                    registration,
                    context,
                    HookOutcome.TIMED_OUT,
                    started,
                    failure_code="hook_timeout",
                )
                if not self._publish_pre(record):
                    return _audit_block(registration.hook_id)
                return HookGateResult(
                    allowed=False,
                    hook_id=registration.hook_id,
                    failure_code="hook_timeout",
                )
            except Exception:
                record = self._record(
                    registration,
                    context,
                    HookOutcome.FAILED,
                    started,
                    failure_code="hook_failed",
                )
                if not self._publish_pre(record):
                    return _audit_block(registration.hook_id)
                return HookGateResult(
                    allowed=False,
                    hook_id=registration.hook_id,
                    failure_code="hook_failed",
                )
            if type(candidate) is not PreToolHookResult:
                record = self._record(
                    registration,
                    context,
                    HookOutcome.FAILED,
                    started,
                    failure_code="invalid_hook_result",
                )
                if not self._publish_pre(record):
                    return _audit_block(registration.hook_id)
                return HookGateResult(
                    allowed=False,
                    hook_id=registration.hook_id,
                    failure_code="invalid_hook_result",
                )
            result = candidate
            outcome = (
                HookOutcome.CONTINUED
                if result.decision is HookDecision.CONTINUE
                else HookOutcome.BLOCKED
            )
            if not self._publish_pre(self._record(registration, context, outcome, started)):
                return _audit_block(registration.hook_id)
            if result.decision is HookDecision.BLOCK:
                return HookGateResult(allowed=False, hook_id=registration.hook_id)
        return HookGateResult(allowed=True)

    async def after_tool(self, context: PostToolHookContext) -> None:
        for registration in self._post:
            started = self._monotonic_ns()
            failure_code: str | None = None
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    candidate = cast(
                        object,
                        await cast(PostToolHook, registration.handler).after_tool(context),
                    )
                if candidate is not None:
                    failure_code = "invalid_hook_result"
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                outcome = HookOutcome.TIMED_OUT
                failure_code = "hook_timeout"
            except Exception:
                outcome = HookOutcome.FAILED
                failure_code = "hook_failed"
            else:
                outcome = HookOutcome.COMPLETED if failure_code is None else HookOutcome.FAILED
            record = self._record(
                registration,
                context,
                outcome,
                started,
                failure_code=failure_code,
            )
            self._publish_post(record)

    def _record(
        self,
        registration: HookRegistration,
        context: ToolHookContext,
        outcome: HookOutcome,
        started: int,
        *,
        failure_code: str | None = None,
    ) -> HookAuditRecord:
        elapsed_ns = max(0, self._monotonic_ns() - started)
        elapsed_ms = min(30_000, elapsed_ns // 1_000_000)
        return HookAuditRecord(
            hook_id=registration.hook_id,
            source=registration.source,
            phase=registration.phase,
            outcome=outcome,
            tool_call_id=context.call.id,
            tool_name=context.call.name,
            elapsed_ms=elapsed_ms,
            failure_code=failure_code,
        )

    def _publish_pre(self, record: HookAuditRecord) -> bool:
        try:
            self._audit.publish(record)
        except Exception:
            return False
        return True

    def _publish_post(self, record: HookAuditRecord) -> bool:
        try:
            self._audit.publish(record)
        except Exception:
            return False
        return True


def _audit_block(hook_id: str) -> HookGateResult:
    return HookGateResult(
        allowed=False,
        hook_id=hook_id,
        failure_code="hook_audit_failed",
    )
