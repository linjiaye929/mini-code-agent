from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError


class _ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(default=1, ge=1, le=10_000_000)
    max_lines: int = Field(default=200, ge=1, le=2_000)


class ReadFileTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="read_file",
        description="Read a bounded UTF-8 text file from the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1024,
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10_000_000,
                },
                "max_lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2_000,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )

    def __init__(self, workspace: WorkspaceBoundary) -> None:
        self._workspace = workspace

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return self._error(
                call.id,
                "unknown_tool",
                "The requested tool is not read_file.",
            )
        try:
            arguments = _ReadFileArguments.model_validate(call.model_dump(mode="json")["arguments"])
        except ValidationError:
            return self._error(
                call.id,
                "invalid_arguments",
                "read_file arguments are invalid.",
            )

        try:
            source = self._workspace.read_text(arguments.path)
        except WorkspaceError as exc:
            return self._error(
                call.id,
                exc.code.value,
                exc.public_message,
            )

        lines = source.text.splitlines(keepends=True)
        start_index = arguments.start_line - 1
        selected = lines[start_index : start_index + arguments.max_lines]
        end_line = min(source.line_count, start_index + len(selected))
        content = {
            "content": "".join(selected),
            "end_line": end_line,
            "path": source.path,
            "start_line": arguments.start_line,
            "total_lines": source.line_count,
            "truncated": arguments.start_line > 1 or end_line < source.line_count,
        }
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                content,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
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
