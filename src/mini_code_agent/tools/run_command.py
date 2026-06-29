from __future__ import annotations

import json
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mini_code_agent.command.errors import CommandError
from mini_code_agent.command.models import CommandRequest
from mini_code_agent.command.runner import CommandRunner
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import ActionPreview, RiskLevel
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError


class _RunCommandArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd: str = Field(default=".", min_length=1, max_length=1024)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_argv(self) -> Self:
        if not self.argv[0] or any(
            len(argument) > 4096 or "\0" in argument for argument in self.argv
        ):
            raise ValueError("argv is invalid")
        return self


class RunCommandTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="run_command",
        description=(
            "Run one argv-based command in a workspace directory with bounded time and output."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "prefixItems": [
                        {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                        }
                    ],
                    "items": {"type": "string", "maxLength": 4096},
                },
                "cwd": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1024,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
            },
            "required": ["argv", "reason"],
            "additionalProperties": False,
        },
        side_effect=SideEffect.EXECUTE,
    )

    def __init__(
        self,
        workspace: WorkspaceBoundary,
        runner: CommandRunner,
    ) -> None:
        self._workspace = workspace
        self._runner = runner

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        arguments, request = self._prepare_request(call)
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=self._definition.name,
            side_effect=self._definition.side_effect,
            risk=RiskLevel.CRITICAL,
            summary="Run one local argv command.",
            reason=arguments.reason,
            resources=(request.cwd_display,),
            command=request.argv,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            _, request = self._prepare_request(call)
        except (ValidationError, ValueError):
            return self._error(
                call.id,
                "invalid_arguments",
                "run_command arguments are invalid.",
            )
        except WorkspaceError as exc:
            return self._error(call.id, exc.code.value, exc.public_message)

        try:
            result = await self._runner.run(request)
        except CommandError as exc:
            return self._error(call.id, exc.code.value, exc.public_message)
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def _prepare_request(
        self,
        call: ToolCall,
    ) -> tuple[_RunCommandArguments, CommandRequest]:
        if call.name != self._definition.name:
            raise ValueError("Unexpected tool name.")
        arguments = _RunCommandArguments.model_validate(call.model_dump(mode="json")["arguments"])
        if arguments.timeout_seconds > self._runner.limits.max_timeout_seconds:
            raise ValueError("Requested timeout exceeds runner limit.")
        cwd, cwd_display = self._workspace.resolve_directory(arguments.cwd)
        return arguments, CommandRequest(
            argv=arguments.argv,
            cwd=cwd,
            cwd_display=cwd_display,
            timeout_seconds=arguments.timeout_seconds,
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
