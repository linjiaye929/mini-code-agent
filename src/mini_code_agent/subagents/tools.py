from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import ActionPreview, RiskLevel
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.models import (
    SubagentBatchResult,
    SubagentError,
    SubagentErrorCode,
    SubagentProfile,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition

_INVALID_ARGUMENTS_MESSAGE = "Subagent Tool arguments were invalid."
_FAILED_MESSAGE = "Subagent batch execution failed."
_COMPOSITION_MESSAGE = "Subagent capabilities did not match the host profile."
_RESULT_TOO_LARGE_MESSAGE = "Subagent batch result exceeded the configured size limit."
_PROFILE_CONFLICT_MESSAGE = "Subagent Tool profiles conflict."


class _SubagentBatchRunner(Protocol):
    @property
    def profile(self) -> SubagentProfile: ...

    async def run_batch(
        self,
        *,
        parent_tool_call_id: str,
        tasks: tuple[str, ...],
    ) -> SubagentBatchResult: ...


class _AnalysisArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tasks: tuple[str, ...] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("tasks")
    @classmethod
    def reject_invalid_tasks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any("\0" in task for task in value):
            raise ValueError("Subagent tasks were invalid.")
        return value

    @field_validator("reason")
    @classmethod
    def reject_invalid_reason(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("Subagent reason was invalid.")
        return value


class SubagentAnalysisTool:
    def __init__(self, supervisor: _SubagentBatchRunner) -> None:
        self._supervisor = supervisor
        self._profile = supervisor.profile
        self._definition = ToolDefinition(
            name=self._profile.local_name,
            description=self._profile.description,
            input_schema=_input_schema(self._profile),
            side_effect=SideEffect.READ_ONLY,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        if call.name != self._definition.name:
            raise ValueError("The requested Subagent Tool is not registered.")
        arguments = self._parse_arguments(call)
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=self._definition.name,
            side_effect=SideEffect.READ_ONLY,
            risk=RiskLevel.MEDIUM,
            summary=(
                f"Delegate {len(arguments.tasks)} isolated analysis "
                f"task(s) to profile {self._profile.profile_id}."
            ),
            reason=arguments.reason,
            resources=(".",),
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return _error(
                call.id,
                "unknown_tool",
                "The requested Subagent Tool is not registered.",
            )
        try:
            arguments = self._parse_arguments(call)
        except ValueError:
            return _error(
                call.id,
                "invalid_arguments",
                _INVALID_ARGUMENTS_MESSAGE,
            )

        try:
            candidate = await self._supervisor.run_batch(
                parent_tool_call_id=call.id,
                tasks=arguments.tasks,
            )
            batch = _validated_batch(
                candidate,
                profile=self._profile,
                expected_children=len(arguments.tasks),
            )
            content = _serialize_batch(
                batch,
                max_result_bytes=self._profile.limits.max_result_bytes,
            )
        except asyncio.CancelledError:
            raise
        except SubagentError as exc:
            return _error(call.id, exc.code.value, exc.public_message)
        except SubagentCompositionError:
            return _error(
                call.id,
                SubagentErrorCode.COMPOSITION_FAILED.value,
                _COMPOSITION_MESSAGE,
            )
        except _ResultTooLarge:
            return _error(
                call.id,
                SubagentErrorCode.RESULT_TOO_LARGE.value,
                _RESULT_TOO_LARGE_MESSAGE,
            )
        except Exception:
            return _error(
                call.id,
                SubagentErrorCode.CHILD_FAILED.value,
                _FAILED_MESSAGE,
            )
        return ToolResult(tool_call_id=call.id, content=content)

    def _parse_arguments(self, call: ToolCall) -> _AnalysisArguments:
        try:
            arguments = _AnalysisArguments.model_validate(
                dict(call.arguments),
                strict=True,
            )
        except ValidationError:
            raise ValueError(_INVALID_ARGUMENTS_MESSAGE) from None
        limits = self._profile.limits
        if len(arguments.tasks) > limits.max_tasks or any(
            len(task) > limits.max_task_chars for task in arguments.tasks
        ):
            raise ValueError(_INVALID_ARGUMENTS_MESSAGE)
        return arguments


def build_subagent_tools(
    supervisors: Iterable[_SubagentBatchRunner],
) -> tuple[SubagentAnalysisTool, ...]:
    ordered = tuple(supervisors)
    profiles = tuple(supervisor.profile for supervisor in ordered)
    profile_ids = tuple(profile.profile_id for profile in profiles)
    local_names = tuple(profile.local_name for profile in profiles)
    child_tool_names = {tool_name for profile in profiles for tool_name in profile.tool_names}
    if (
        len(set(profile_ids)) != len(profile_ids)
        or len(set(local_names)) != len(local_names)
        or not set(local_names).isdisjoint(child_tool_names)
    ):
        raise ValueError(_PROFILE_CONFLICT_MESSAGE)
    return tuple(SubagentAnalysisTool(supervisor) for supervisor in ordered)


class _ResultTooLarge(ValueError):
    pass


def _input_schema(profile: SubagentProfile) -> dict[str, JsonValue]:
    no_nul = r"^[^\u0000]+$"
    return {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": profile.limits.max_tasks,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": profile.limits.max_task_chars,
                    "pattern": no_nul,
                },
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "pattern": no_nul,
            },
        },
        "required": ["tasks", "reason"],
        "additionalProperties": False,
    }


def _validated_batch(
    candidate: object,
    *,
    profile: SubagentProfile,
    expected_children: int,
) -> SubagentBatchResult:
    if not isinstance(candidate, SubagentBatchResult):
        raise ValueError("Invalid Subagent batch result.")
    batch = SubagentBatchResult.model_validate(candidate.model_dump(mode="json"))
    if batch.profile_id != profile.profile_id or len(batch.children) != expected_children:
        raise ValueError("Invalid Subagent batch result.")
    return batch


def _serialize_batch(
    batch: SubagentBatchResult,
    *,
    max_result_bytes: int,
) -> str:
    payload = {
        "content_type": "subagent_batch_result",
        **batch.model_dump(mode="json"),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Invalid Subagent batch result.") from None
    if len(encoded) > max_result_bytes:
        raise _ResultTooLarge
    return encoded.decode("ascii")


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
