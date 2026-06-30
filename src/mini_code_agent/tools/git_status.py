from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.git.client import GitStatusReader
from mini_code_agent.git.errors import GitError
from mini_code_agent.tools.base import SideEffect, ToolDefinition


class _GitStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitStatusTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="git_status",
        description="Read bounded machine-parsed Git branch and working-tree status.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )

    def __init__(self, git: GitStatusReader) -> None:
        self._git = git

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return _error(
                call.id,
                "unknown_tool",
                "The requested tool is not git_status.",
            )
        try:
            _GitStatusArguments.model_validate(call.model_dump(mode="json")["arguments"])
        except ValidationError:
            return _error(
                call.id,
                "invalid_arguments",
                "git_status arguments are invalid.",
            )
        try:
            snapshot = await self._git.status()
        except GitError as exc:
            return _error(call.id, exc.code.value, exc.public_message)
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )


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
