from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import ActionPreview, RiskLevel, TrustSource
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.models import SubagentProfile
from mini_code_agent.tools.base import SideEffect, ToolDefinition, ToolExecutor
from mini_code_agent.worktrees.models import (
    ImplementationRunResult,
    WorktreeProfile,
)

_REQUIRED_IMPLEMENTATION_TOOLS = (
    "read_file",
    "search_text",
    "write_file",
    "edit_file",
)
_OPTIONAL_TEST_TOOL = "run_tests"
_EXPECTED_SIDE_EFFECTS = {
    "read_file": SideEffect.READ_ONLY,
    "search_text": SideEffect.READ_ONLY,
    "write_file": SideEffect.WRITE,
    "edit_file": SideEffect.WRITE,
    "run_tests": SideEffect.EXECUTE,
}
_INVALID_ARGUMENTS = "Implementation delegation arguments were invalid."
_FAILED = "Implementation delegation failed."


class _ImplementationRunner(Protocol):
    @property
    def profile(self) -> WorktreeProfile: ...

    async def run(
        self,
        *,
        parent_tool_call_id: str,
        task: str,
    ) -> ImplementationRunResult: ...


class _ImplementationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(min_length=1, max_length=500)


class DelegateImplementationTool:
    def __init__(self, runner: _ImplementationRunner) -> None:
        self._runner = runner
        self._profile = runner.profile
        implementation = self._profile.implementation_profile
        self._definition = ToolDefinition(
            name=implementation.local_name,
            description=implementation.description,
            input_schema=_implementation_input_schema(self._profile),
            side_effect=SideEffect.EXECUTE,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        arguments = self._parse(call)
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=self._definition.name,
            side_effect=SideEffect.EXECUTE,
            risk=RiskLevel.CRITICAL,
            summary="Run one bounded implementation child in an isolated Worktree.",
            reason=arguments.reason,
            resources=(".",),
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            arguments = self._parse(call)
        except ValueError:
            return _tool_error(call.id, "invalid_arguments", _INVALID_ARGUMENTS)
        try:
            candidate = await self._runner.run(
                parent_tool_call_id=call.id,
                task=arguments.task,
            )
            result = ImplementationRunResult.model_validate(candidate.model_dump(mode="json"))
            content = _serialize_implementation_result(
                result,
                max_result_bytes=(self._profile.implementation_profile.limits.max_result_bytes),
            )
        except asyncio.CancelledError:
            raise
        except SubagentCompositionError:
            return _tool_error(
                call.id,
                "composition_failed",
                "Implementation child composition failed.",
            )
        except ValueError:
            return _tool_error(
                call.id,
                "result_too_large",
                "Implementation result exceeded its bounded contract.",
            )
        except Exception:
            return _tool_error(call.id, "child_failed", _FAILED)
        return ToolResult(tool_call_id=call.id, content=content)

    def _parse(self, call: ToolCall) -> _ImplementationArguments:
        if call.name != self._definition.name:
            raise ValueError(_INVALID_ARGUMENTS)
        try:
            arguments = _ImplementationArguments.model_validate(
                dict(call.arguments),
                strict=True,
            )
        except ValidationError:
            raise ValueError(_INVALID_ARGUMENTS) from None
        if (
            len(arguments.task) > self._profile.implementation_profile.limits.max_task_chars
            or "\0" in arguments.task
            or "\0" in arguments.reason
        ):
            raise ValueError(_INVALID_ARGUMENTS)
        return arguments


def build_worktree_tools(
    runners: Iterable[_ImplementationRunner],
) -> tuple[DelegateImplementationTool, ...]:
    ordered = tuple(runners)
    profiles = tuple(runner.profile.implementation_profile for runner in ordered)
    if (
        len({profile.profile_id for profile in profiles}) != len(profiles)
        or len({profile.local_name for profile in profiles}) != len(profiles)
        or any(profile.mode != "implementation" for profile in profiles)
    ):
        raise ValueError("Implementation Tool profiles conflict.")
    return tuple(DelegateImplementationTool(runner) for runner in ordered)


def validate_implementation_child_tools(
    profile: SubagentProfile,
    tools: ToolExecutor,
) -> None:
    try:
        definitions = tools.definitions
        names = tuple(definition.name for definition in definitions)
        accepted_names = {
            _REQUIRED_IMPLEMENTATION_TOOLS,
            (*_REQUIRED_IMPLEMENTATION_TOOLS, _OPTIONAL_TEST_TOOL),
        }
        if (
            profile.mode != "implementation"
            or profile.tool_names not in accepted_names
            or names != profile.tool_names
            or any(
                definition.side_effect is not _EXPECTED_SIDE_EFFECTS.get(definition.name)
                for definition in definitions
            )
            or getattr(tools, "governance_enforced", None) is not True
        ):
            raise SubagentCompositionError
        trust_source_for = getattr(tools, "trust_source_for", None)
        if not callable(trust_source_for):
            raise SubagentCompositionError
        if any(trust_source_for(name) is not TrustSource.SUBAGENT for name in names):
            raise SubagentCompositionError
    except SubagentCompositionError:
        raise
    except Exception:
        raise SubagentCompositionError from None


def _implementation_input_schema(profile: WorktreeProfile) -> dict[str, JsonValue]:
    no_nul = r"^[^\u0000]+$"
    return {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "minLength": 1,
                "maxLength": profile.implementation_profile.limits.max_task_chars,
                "pattern": no_nul,
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "pattern": no_nul,
            },
        },
        "required": ["task", "reason"],
        "additionalProperties": False,
    }


def _serialize_implementation_result(
    result: ImplementationRunResult,
    *,
    max_result_bytes: int,
) -> str:
    snapshot = result.finalization.snapshot
    manifest = snapshot.manifest
    candidate: dict[str, object] | None = None
    if manifest is not None:
        candidate = {
            "after_content_bytes": manifest.after_content_bytes,
            "base_sha": manifest.base_sha,
            "candidate_id": manifest.candidate_id,
            "changed_files": manifest.changed_files,
            "disposition": manifest.disposition.value,
            "files": [
                {
                    "after_sha256": item.after_sha256,
                    "before_sha256": item.before_sha256,
                    "byte_count": item.byte_count,
                    "line_count": item.line_count,
                    "operation": item.operation.value,
                    "path": item.path,
                }
                for item in manifest.files
            ],
            "manifest_sha256": manifest.manifest_sha256,
            "rejection_reasons": list(manifest.rejection_reasons),
        }
    payload = {
        "candidate": candidate,
        "child": {
            "child_id": result.child.child_id,
            "error_code": (
                result.child.error_code.value if result.child.error_code is not None else None
            ),
            "evidence": [item.model_dump(mode="json") for item in result.child.evidence],
            "result_sha256": result.child.result_sha256,
            "status": result.child.status.value,
            "stop_reason": (
                result.child.stop_reason.value if result.child.stop_reason is not None else None
            ),
            "tool_calls": result.child.tool_calls,
            "turns": result.child.turns,
            "usage": result.child.usage.model_dump(mode="json"),
        },
        "cleanup_status": result.finalization.cleanup.status.value,
        "content_type": "governed_worktree_result",
        "duration_ms": result.duration_ms,
        "lease_id": result.finalization.lease_id,
        "profile_id": result.profile_id,
        "result_sha256": result.result_sha256,
        "snapshot_status": snapshot.status.value,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > max_result_bytes:
        raise ValueError("Implementation result is too large.")
    return encoded.decode("ascii")


def _tool_error(call_id: str, code: str, message: str) -> ToolResult:
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
