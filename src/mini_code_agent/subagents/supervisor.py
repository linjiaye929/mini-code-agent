from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from mini_code_agent.agent.models import AgentResult, StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.providers.base import ProviderCapabilities, TokenUsage
from mini_code_agent.subagents.contracts import (
    SubagentCompositionError,
    SubagentProviderFactory,
    SubagentToolFactory,
    validate_child_tools,
)
from mini_code_agent.subagents.events import (
    NullSubagentEventSink,
    SubagentBatchCompleted,
    SubagentBatchStarted,
    SubagentCompleted,
    SubagentEvent,
    SubagentEventSink,
    SubagentStarted,
)
from mini_code_agent.subagents.evidence import (
    SubagentEvidenceError,
    extract_subagent_evidence,
)
from mini_code_agent.subagents.models import (
    SubagentBatchResult,
    SubagentChildResult,
    SubagentError,
    SubagentErrorCode,
    SubagentProfile,
    SubagentStatus,
)

_CHILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_INVALID_BATCH_MESSAGE = "Subagent batch request was invalid."
_CHILD_TIMEOUT_MESSAGE = "Subagent execution timed out."
_CHILD_FAILED_MESSAGE = "Subagent execution failed."


@dataclass(frozen=True, slots=True)
class _PreparedChild:
    child_id: str
    ordinal: int
    task: str
    runtime: AgentRuntime


class SubagentSupervisor:
    def __init__(
        self,
        profile: SubagentProfile,
        *,
        workspace_root: Path,
        provider_factory: SubagentProviderFactory,
        tool_factory: SubagentToolFactory,
        events: SubagentEventSink | None = None,
        id_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        try:
            root = workspace_root.resolve(strict=True)
        except OSError:
            raise ValueError("Subagent workspace root must exist.") from None
        if not root.is_dir():
            raise ValueError("Subagent workspace root must be a directory.")
        self._profile = profile
        self._workspace_root = root
        self._provider_factory = provider_factory
        self._tool_factory = tool_factory
        self._events = events or NullSubagentEventSink()
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._monotonic = monotonic

    @property
    def profile(self) -> SubagentProfile:
        return self._profile

    async def run_batch(
        self,
        *,
        parent_tool_call_id: str,
        tasks: tuple[str, ...],
    ) -> SubagentBatchResult:
        self._validate_batch(parent_tool_call_id, tasks)
        children = self._prepare_children(tasks)
        started_at = self._monotonic()
        self._publish(
            SubagentBatchStarted(
                parent_tool_call_id=parent_tool_call_id,
                profile_id=self._profile.profile_id,
                task_count=len(tasks),
            )
        )
        result_slots: list[SubagentChildResult | None] = [None] * len(children)
        semaphore = asyncio.Semaphore(self._profile.limits.max_concurrency)

        async def run_at(child: _PreparedChild) -> None:
            result_slots[child.ordinal] = await self._run_child(
                parent_tool_call_id=parent_tool_call_id,
                child=child,
                semaphore=semaphore,
            )

        try:
            async with asyncio.timeout(self._profile.limits.batch_timeout_seconds):
                async with asyncio.TaskGroup() as group:
                    for child in children:
                        group.create_task(run_at(child))
        except TimeoutError:
            duration_ms = _elapsed_ms(started_at, self._monotonic())
            for child in children:
                if result_slots[child.ordinal] is not None:
                    continue
                result = self._error_result(
                    child,
                    status=SubagentStatus.BATCH_TIMED_OUT,
                    code=SubagentErrorCode.BATCH_TIMEOUT,
                    message="Subagent batch timed out.",
                )
                result_slots[child.ordinal] = result
                self._publish_child_completed(
                    parent_tool_call_id=parent_tool_call_id,
                    result=result,
                    duration_ms=duration_ms,
                )

        duration_ms = _elapsed_ms(started_at, self._monotonic())
        if any(result is None for result in result_slots):
            raise RuntimeError("Subagent result slot was not populated.")
        results = cast(tuple[SubagentChildResult, ...], tuple(result_slots))
        batch = SubagentBatchResult.from_children(
            profile_id=self._profile.profile_id,
            children=results,
            duration_ms=duration_ms,
        )
        self._publish(
            SubagentBatchCompleted(
                parent_tool_call_id=parent_tool_call_id,
                profile_id=self._profile.profile_id,
                duration_ms=batch.duration_ms,
                completed=batch.completed,
                stopped=batch.stopped,
                timed_out=batch.timed_out,
                failed=batch.failed,
                result_sha256=batch.result_sha256,
            )
        )
        return batch

    def _prepare_children(
        self,
        tasks: tuple[str, ...],
    ) -> tuple[_PreparedChild, ...]:
        prepared: list[_PreparedChild] = []
        provider_ids: set[int] = set()
        tool_ids: set[int] = set()
        try:
            child_ids = tuple(self._id_factory() for _ in tasks)
            if len(set(child_ids)) != len(child_ids) or any(
                _CHILD_ID.fullmatch(child_id) is None for child_id in child_ids
            ):
                raise SubagentCompositionError
            for ordinal, (task, child_id) in enumerate(zip(tasks, child_ids, strict=True)):
                provider = self._provider_factory.create(self._profile, child_id)
                tools = self._tool_factory.create(self._profile, self._workspace_root)
                if id(provider) in provider_ids or id(tools) in tool_ids:
                    raise SubagentCompositionError
                _validate_provider(provider)
                validate_child_tools(self._profile, tools)
                provider_ids.add(id(provider))
                tool_ids.add(id(tools))
                prepared.append(
                    _PreparedChild(
                        child_id=child_id,
                        ordinal=ordinal,
                        task=task,
                        runtime=AgentRuntime(
                            provider,
                            tools,
                            limits=self._profile.agent_limits,
                        ),
                    )
                )
        except asyncio.CancelledError:
            raise
        except SubagentCompositionError:
            raise
        except Exception:
            raise SubagentCompositionError from None
        return tuple(prepared)

    async def _run_child(
        self,
        *,
        parent_tool_call_id: str,
        child: _PreparedChild,
        semaphore: asyncio.Semaphore,
    ) -> SubagentChildResult:
        started_at = self._monotonic()
        self._publish(
            SubagentStarted(
                parent_tool_call_id=parent_tool_call_id,
                profile_id=self._profile.profile_id,
                child_id=child.child_id,
                ordinal=child.ordinal,
            )
        )
        try:
            async with semaphore:
                async with asyncio.timeout(self._profile.limits.child_timeout_seconds):
                    candidate = cast(
                        object,
                        await child.runtime.run(
                            user_prompt=child.task,
                            system_prompt=self._profile.system_prompt,
                            run_id=_runtime_id(child.child_id),
                        ),
                    )
            if not isinstance(candidate, AgentResult):
                raise TypeError("invalid Agent result")
            result = self._project_agent_result(child, candidate)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            result = self._error_result(
                child,
                status=SubagentStatus.TIMED_OUT,
                code=SubagentErrorCode.CHILD_TIMEOUT,
                message=_CHILD_TIMEOUT_MESSAGE,
            )
        except Exception:
            result = self._error_result(
                child,
                status=SubagentStatus.FAILED,
                code=SubagentErrorCode.CHILD_FAILED,
                message=_CHILD_FAILED_MESSAGE,
            )
        duration_ms = _elapsed_ms(started_at, self._monotonic())
        self._publish_child_completed(
            parent_tool_call_id=parent_tool_call_id,
            result=result,
            duration_ms=duration_ms,
        )
        return result

    def _publish_child_completed(
        self,
        *,
        parent_tool_call_id: str,
        result: SubagentChildResult,
        duration_ms: int,
    ) -> None:
        self._publish(
            SubagentCompleted(
                parent_tool_call_id=parent_tool_call_id,
                profile_id=self._profile.profile_id,
                child_id=result.child_id,
                ordinal=result.ordinal,
                status=result.status,
                duration_ms=duration_ms,
                turns=result.turns,
                tool_calls=result.tool_calls,
                usage=result.usage,
                result_sha256=result.result_sha256,
            )
        )

    def _project_agent_result(
        self,
        child: _PreparedChild,
        result: AgentResult,
    ) -> SubagentChildResult:
        evidence = extract_subagent_evidence(
            result,
            max_items=self._profile.limits.max_evidence_items,
        )
        if result.turns > self._profile.agent_limits.max_turns:
            raise SubagentEvidenceError
        if result.tool_calls > self._profile.agent_limits.max_tool_calls:
            raise SubagentEvidenceError
        status = (
            SubagentStatus.COMPLETED
            if result.stop_reason is StopReason.COMPLETED
            else SubagentStatus.STOPPED
        )
        summary = result.final_text
        if summary is not None:
            summary = summary[: self._profile.limits.max_summary_chars]
            if "\0" in summary:
                raise SubagentEvidenceError
        projection: dict[str, object] = {
            "child_id": child.child_id,
            "ordinal": child.ordinal,
            "profile_id": self._profile.profile_id,
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

    def _error_result(
        self,
        child: _PreparedChild,
        *,
        status: SubagentStatus,
        code: SubagentErrorCode,
        message: str,
    ) -> SubagentChildResult:
        projection: dict[str, object] = {
            "child_id": child.child_id,
            "ordinal": child.ordinal,
            "profile_id": self._profile.profile_id,
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

    def _validate_batch(
        self,
        parent_tool_call_id: str,
        tasks: tuple[str, ...],
    ) -> None:
        limits = self._profile.limits
        valid_parent = _valid_parent_tool_call_id(parent_tool_call_id)
        valid_tasks = _valid_tasks(
            tasks,
            max_tasks=limits.max_tasks,
            max_task_chars=limits.max_task_chars,
        )
        if not valid_parent or not valid_tasks:
            raise SubagentError(
                SubagentErrorCode.INVALID_BATCH,
                _INVALID_BATCH_MESSAGE,
            )

    def _publish(self, event: SubagentEvent) -> None:
        try:
            self._events.publish(event)
        except Exception:
            return


def _validate_provider(provider: object) -> None:
    capabilities = getattr(provider, "capabilities", None)
    if (
        not isinstance(capabilities, ProviderCapabilities)
        or not callable(getattr(provider, "complete", None))
        or not callable(getattr(provider, "stream", None))
    ):
        raise SubagentCompositionError


def _valid_parent_tool_call_id(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and "\0" not in value


def _valid_tasks(
    value: object,
    *,
    max_tasks: int,
    max_task_chars: int,
) -> bool:
    if not isinstance(value, tuple):
        return False
    tasks = cast(tuple[object, ...], value)
    if not 1 <= len(tasks) <= max_tasks or not all(
        isinstance(task, str) and 1 <= len(task) <= max_task_chars and "\0" not in task
        for task in tasks
    ):
        return False
    string_tasks = cast(tuple[str, ...], tasks)
    return len(set(string_tasks)) == len(string_tasks)


def _runtime_id(child_id: str) -> str:
    digest = hashlib.sha256(child_id.encode("utf-8")).hexdigest()[:32]
    return f"subagent-{digest}"


def _elapsed_ms(started_at: float, completed_at: float) -> int:
    return max(0, int((completed_at - started_at) * 1000))


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
