from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.git.client import GitDiffReader
from mini_code_agent.git.errors import GitError
from mini_code_agent.tools.base import SideEffect, ToolDefinition


class _GitDiffArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged: StrictBool = False


class GitDiffTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="git_diff",
        description="Read a bounded hardened staged or unstaged Git patch.",
        input_schema={
            "type": "object",
            "properties": {
                "staged": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )

    def __init__(self, git: GitDiffReader) -> None:
        self._git = git

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return _error(
                call.id,
                "unknown_tool",
                "The requested tool is not git_diff.",
            )
        try:
            arguments = _GitDiffArguments.model_validate(call.model_dump(mode="json")["arguments"])
        except ValidationError:
            return _error(
                call.id,
                "invalid_arguments",
                "git_diff arguments are invalid.",
            )
        try:
            result = await self._git.diff(staged=arguments.staged)
        except GitError as exc:
            return _error(call.id, exc.code.value, exc.public_message)
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                result.model_dump(mode="json"),
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
