from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, cast
from uuid import uuid4

from mini_code_agent.agent.models import AgentResult, StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.providers.base import ProviderCapabilities, TokenUsage
from mini_code_agent.subagents.contracts import (
    SubagentCompositionError,
    SubagentProviderFactory,
)
from mini_code_agent.subagents.evidence import (
    SubagentEvidenceError,
    extract_subagent_evidence,
)
from mini_code_agent.subagents.models import (
    SubagentChildResult,
    SubagentErrorCode,
    SubagentProfile,
    SubagentStatus,
)
from mini_code_agent.tools.base import ToolExecutor
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.models import WorkspaceLimits
from mini_code_agent.worktrees.finalization import (
    WorktreeFinalizer,
    run_cancellation_finalization,
)
from mini_code_agent.worktrees.ledger import (
    LedgerRecordingToolExecutor,
    MutationLedger,
)
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import (
    ImplementationRunResult,
    WorktreeFinalizationResult,
    WorktreeLease,
    WorktreeProfile,
)
from mini_code_agent.worktrees.tools import validate_implementation_child_tools

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class WorktreeChildToolFactory(Protocol):
    def create(
        self,
        profile: SubagentProfile,
        workspace: WorkspaceBoundary,
    ) -> ToolExecutor: ...


class WorktreeImplementationRunner:
    def __init__(
        self,
        profile: WorktreeProfile,
        *,
        manager: WorktreeManager,
        finalizer: WorktreeFinalizer,
        provider_factory: SubagentProviderFactory,
        tool_factory: WorktreeChildToolFactory,
        id_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if profile.implementation_profile.mode != "implementation":
            raise ValueError("Worktree runner requires an implementation profile.")
        self._profile = profile
        self._manager = manager
        self._finalizer = finalizer
        self._provider_factory = provider_factory
        self._tool_factory = tool_factory
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._monotonic = monotonic

    @property
    def profile(self) -> WorktreeProfile:
        return self._profile

    async def run(
        self,
        *,
        parent_tool_call_id: str,
        task: str,
    ) -> ImplementationRunResult:
        implementation = self._profile.implementation_profile
        if (
            not 1 <= len(parent_tool_call_id) <= 128
            or "\0" in parent_tool_call_id
            or not 1 <= len(task) <= implementation.limits.max_task_chars
            or "\0" in task
        ):
            raise ValueError("Implementation delegation request is invalid.")
        child_id, candidate_id = self._allocate_ids()
        started_at = self._monotonic()
        lease = await self._manager.create_lease(child_id=child_id)
        ledger = MutationLedger(max_entries=implementation.agent_limits.max_tool_calls)
        try:
            runtime = self._compose_runtime(lease, ledger)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = _error_child(
                implementation,
                child_id,
                status=SubagentStatus.FAILED,
                code=SubagentErrorCode.CHILD_FAILED,
                message="Implementation child composition failed.",
            )
            await self._finalize_after_child(
                lease,
                ledger,
                candidate_id=candidate_id,
                child_status=failed.status,
                evidence_sha256=failed.result_sha256,
            )
            raise SubagentCompositionError from None

        async def finalize_cancelled() -> object:
            failed = _error_child(
                implementation,
                child_id,
                status=SubagentStatus.FAILED,
                code=SubagentErrorCode.CHILD_FAILED,
                message="Implementation child was cancelled.",
            )
            return await self._finalizer.finalize(
                lease,
                ledger,
                candidate_id=candidate_id,
                child_status=failed.status,
                evidence_sha256=failed.result_sha256,
            )

        try:
            async with asyncio.timeout(implementation.limits.child_timeout_seconds):
                candidate = cast(
                    object,
                    await runtime.run(
                        user_prompt=task,
                        system_prompt=implementation.system_prompt,
                        run_id=_runtime_id(child_id),
                    ),
                )
            if not isinstance(candidate, AgentResult):
                raise TypeError("Invalid implementation Agent result.")
            child = _project_agent_result(implementation, child_id, candidate)
        except asyncio.CancelledError:
            await run_cancellation_finalization(
                finalize=finalize_cancelled,
                timeout_seconds=self._profile.limits.cleanup_timeout_seconds,
                on_timeout=lambda: self._manager.record_cancellation_timeout(lease),
            )
            raise
        except TimeoutError:
            child = _error_child(
                implementation,
                child_id,
                status=SubagentStatus.TIMED_OUT,
                code=SubagentErrorCode.CHILD_TIMEOUT,
                message="Implementation child timed out.",
            )
        except Exception:
            child = _error_child(
                implementation,
                child_id,
                status=SubagentStatus.FAILED,
                code=SubagentErrorCode.CHILD_FAILED,
                message="Implementation child failed.",
            )

        finalization = await self._finalize_after_child(
            lease,
            ledger,
            candidate_id=candidate_id,
            child_status=child.status,
            evidence_sha256=child.result_sha256,
        )
        return ImplementationRunResult.create(
            profile_id=implementation.profile_id,
            child=child,
            finalization=finalization,
            duration_ms=_elapsed_ms(started_at, self._monotonic()),
        )

    async def _finalize_after_child(
        self,
        lease: WorktreeLease,
        ledger: MutationLedger,
        *,
        candidate_id: str,
        child_status: SubagentStatus,
        evidence_sha256: str,
    ) -> WorktreeFinalizationResult:
        task = asyncio.create_task(
            self._finalizer.finalize(
                lease,
                ledger,
                candidate_id=candidate_id,
                child_status=child_status,
                evidence_sha256=evidence_sha256,
            )
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            with suppress(Exception):
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self._profile.limits.cleanup_timeout_seconds,
                    )
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    self._manager.record_cancellation_timeout(lease)
            raise

    def _allocate_ids(self) -> tuple[str, str]:
        child_id = self._id_factory()
        candidate_id = self._id_factory()
        if (
            child_id == candidate_id
            or _IDENTIFIER.fullmatch(child_id) is None
            or _IDENTIFIER.fullmatch(candidate_id) is None
        ):
            raise SubagentCompositionError
        return child_id, candidate_id

    def _compose_runtime(
        self,
        lease: WorktreeLease,
        ledger: MutationLedger,
    ) -> AgentRuntime:
        implementation = self._profile.implementation_profile
        workspace = WorkspaceBoundary(
            lease.worktree_path,
            limits=WorkspaceLimits(
                max_file_bytes=self._profile.limits.max_file_bytes,
                max_path_chars=self._profile.limits.max_path_chars,
                max_write_bytes=self._profile.limits.max_file_bytes,
                max_diff_chars=self._profile.limits.max_diff_chars,
            ),
        )
        tools = self._tool_factory.create(implementation, workspace)
        validate_implementation_child_tools(implementation, tools)
        provider = self._provider_factory.create(implementation, lease.child_id)
        _validate_provider(provider)
        return AgentRuntime(
            provider,
            LedgerRecordingToolExecutor(tools, ledger),
            limits=implementation.agent_limits,
        )


def _project_agent_result(
    profile: SubagentProfile,
    child_id: str,
    result: AgentResult,
) -> SubagentChildResult:
    evidence = extract_subagent_evidence(
        result,
        max_items=profile.limits.max_evidence_items,
    )
    if (
        result.turns > profile.agent_limits.max_turns
        or result.tool_calls > profile.agent_limits.max_tool_calls
    ):
        raise SubagentEvidenceError
    status = (
        SubagentStatus.COMPLETED
        if result.stop_reason is StopReason.COMPLETED
        else SubagentStatus.STOPPED
    )
    summary = result.final_text
    if summary is not None:
        summary = summary[: profile.limits.max_summary_chars]
        if "\0" in summary:
            raise SubagentEvidenceError
    projection: dict[str, object] = {
        "child_id": child_id,
        "ordinal": 0,
        "profile_id": profile.profile_id,
        "status": status.value,
        "stop_reason": result.stop_reason.value,
        "turns": result.turns,
        "tool_calls": result.tool_calls,
        "usage": result.usage.model_dump(mode="json"),
        "untrusted_summary": summary,
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "error_code": None,
        "error_message": None,
    }
    return SubagentChildResult.model_validate(
        projection | {"result_sha256": _canonical_sha256(projection)}
    )


def _error_child(
    profile: SubagentProfile,
    child_id: str,
    *,
    status: SubagentStatus,
    code: SubagentErrorCode,
    message: str,
) -> SubagentChildResult:
    projection: dict[str, object] = {
        "child_id": child_id,
        "ordinal": 0,
        "profile_id": profile.profile_id,
        "status": status.value,
        "stop_reason": None,
        "turns": 0,
        "tool_calls": 0,
        "usage": TokenUsage().model_dump(mode="json"),
        "untrusted_summary": None,
        "evidence": [],
        "error_code": code.value,
        "error_message": message,
    }
    return SubagentChildResult.model_validate(
        projection | {"result_sha256": _canonical_sha256(projection)}
    )


def _validate_provider(provider: object) -> None:
    capabilities = getattr(provider, "capabilities", None)
    if (
        not isinstance(capabilities, ProviderCapabilities)
        or not callable(getattr(provider, "complete", None))
        or not callable(getattr(provider, "stream", None))
    ):
        raise SubagentCompositionError


def _runtime_id(child_id: str) -> str:
    digest = hashlib.sha256(child_id.encode("utf-8")).hexdigest()[:32]
    return f"implementation-{digest}"


def _elapsed_ms(started_at: float, completed_at: float) -> int:
    return max(0, min(3_700_000, int((completed_at - started_at) * 1000)))


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
