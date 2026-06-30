from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import ActionPreview, RiskLevel
from mini_code_agent.testing.pytest_runner import PytestRunner
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError, WorkspaceErrorCode


class _RunTestsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: tuple[str, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=32,
    )
    reason: str = Field(min_length=1, max_length=500)


class RunTestsTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="run_tests",
        description=(
            "Run a host-configured Pytest profile for optional workspace-relative targets."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                    },
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        side_effect=SideEffect.EXECUTE,
    )

    def __init__(
        self,
        workspace: WorkspaceBoundary,
        runner: PytestRunner,
    ) -> None:
        if workspace.root != runner.workspace_root:
            raise ValueError("tool and runner workspace roots must match")
        self._workspace = workspace
        self._runner = runner

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        arguments, targets = self._prepare(call)
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=self._definition.name,
            side_effect=self._definition.side_effect,
            risk=RiskLevel.CRITICAL,
            summary="Run the host-configured Pytest profile.",
            reason=arguments.reason,
            resources=targets or (".",),
            command=self._runner.preview_argv(targets),
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            _, targets = self._prepare(call)
        except (ValidationError, ValueError):
            return self._error(
                call.id,
                "invalid_arguments",
                "run_tests arguments are invalid.",
            )
        except WorkspaceError as exc:
            return self._error(call.id, exc.code.value, exc.public_message)

        try:
            result = await self._runner.run(targets)
        except CommandError as exc:
            return self._command_error(call.id, exc.code)
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def prepare_targets(
        self,
        requested: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        candidates = self._runner.profile.default_targets if requested is None else requested
        if len(candidates) > min(32, self._runner.limits.max_targets):
            raise ValueError("test target count exceeds configured limit")

        targets: list[str] = []
        identities: set[str] = set()
        for candidate in candidates:
            if "::" in candidate or any(part.startswith("-") for part in candidate.split("/")):
                raise WorkspaceError(
                    WorkspaceErrorCode.INVALID_PATH,
                    "Workspace path is invalid.",
                )
            resolved, display = self._resolve_target(candidate)
            identity = os.path.normcase(str(resolved))
            if identity in identities:
                raise ValueError("test targets contain a duplicate path")
            identities.add(identity)
            targets.append(display)
        return tuple(targets)

    def _prepare(
        self,
        call: ToolCall,
    ) -> tuple[_RunTestsArguments, tuple[str, ...]]:
        if call.name != self._definition.name:
            raise ValueError("Unexpected tool name.")
        arguments = _RunTestsArguments.model_validate(call.model_dump(mode="json")["arguments"])
        return arguments, self.prepare_targets(arguments.targets)

    def _resolve_target(self, candidate: str) -> tuple[Path, str]:
        try:
            return self._workspace.resolve_directory(candidate)
        except WorkspaceError as directory_error:
            try:
                resolved = self._workspace.resolve_file(candidate)
            except WorkspaceError as file_error:
                if directory_error.code in {
                    WorkspaceErrorCode.INVALID_PATH,
                    WorkspaceErrorCode.OUTSIDE_WORKSPACE,
                    WorkspaceErrorCode.LINK_TRAVERSAL,
                }:
                    raise directory_error from None
                raise file_error from None
            return resolved, self._workspace.relative_path(resolved)

    @classmethod
    def _command_error(
        cls,
        call_id: str,
        code: CommandErrorCode,
    ) -> ToolResult:
        messages = {
            CommandErrorCode.INVALID_REQUEST: "Pytest command request was invalid.",
            CommandErrorCode.COMMAND_NOT_FOUND: "Pytest executable was not found.",
            CommandErrorCode.COMMAND_START_FAILED: "Pytest process could not be started.",
            CommandErrorCode.COMMAND_IO_FAILED: "Pytest process output could not be collected.",
            CommandErrorCode.COMMAND_CLEANUP_FAILED: (
                "Pytest process tree could not be terminated."
            ),
        }
        return cls._error(call_id, code.value, messages[code])

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
