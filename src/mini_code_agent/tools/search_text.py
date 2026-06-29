from __future__ import annotations

import json
from fnmatch import fnmatchcase
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError, WorkspaceErrorCode
from mini_code_agent.workspace.models import SearchLimits


class _SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    glob: str = Field(default="*", min_length=1, max_length=256)
    case_sensitive: bool = True
    max_results: int = Field(default=50, ge=1, le=1_000)


class SearchTextTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="search_text",
        description="Search for bounded literal text within workspace UTF-8 files.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1024,
                },
                "glob": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                },
                "case_sensitive": {"type": "boolean"},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1_000,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )

    def __init__(
        self,
        workspace: WorkspaceBoundary,
        *,
        limits: SearchLimits | None = None,
    ) -> None:
        self._workspace = workspace
        self._limits = limits or SearchLimits()

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return self._error(
                call.id,
                "unknown_tool",
                "The requested tool is not search_text.",
            )
        try:
            arguments = _SearchArguments.model_validate(call.model_dump(mode="json")["arguments"])
        except ValidationError:
            return self._error(
                call.id,
                "invalid_arguments",
                "search_text arguments are invalid.",
            )

        try:
            paths = self._workspace.list_files(
                arguments.path,
                limits=self._limits,
            )
        except WorkspaceError as exc:
            return self._error(call.id, exc.code.value, exc.public_message)

        result_limit = min(arguments.max_results, self._limits.max_results)
        needle = arguments.query if arguments.case_sensitive else arguments.query.casefold()
        matches: list[dict[str, object]] = []
        files_scanned = 0
        skipped_files = 0
        truncated = False

        for path in paths:
            if not fnmatchcase(path, arguments.glob):
                continue
            try:
                source = self._workspace.read_text(path)
            except WorkspaceError as exc:
                if exc.code in {
                    WorkspaceErrorCode.BINARY_FILE,
                    WorkspaceErrorCode.INVALID_ENCODING,
                    WorkspaceErrorCode.TOO_LARGE,
                }:
                    skipped_files += 1
                    continue
                return self._error(call.id, exc.code.value, exc.public_message)

            files_scanned += 1
            for line_number, raw_line in enumerate(
                source.text.splitlines(),
                start=1,
            ):
                line = raw_line
                if len(line) > self._limits.max_line_chars:
                    line = line[: self._limits.max_line_chars]
                    truncated = True
                searchable = line if arguments.case_sensitive else line.casefold()
                offset = 0
                while True:
                    index = searchable.find(needle, offset)
                    if index < 0:
                        break
                    matches.append(
                        {
                            "column": index + 1,
                            "line": line_number,
                            "path": source.path,
                            "preview": _preview(
                                line,
                                index,
                                len(arguments.query),
                                self._limits.max_preview_chars,
                            ),
                        }
                    )
                    if len(matches) >= result_limit:
                        truncated = True
                        return self._success(
                            call.id,
                            arguments.query,
                            matches,
                            files_scanned,
                            skipped_files,
                            truncated,
                        )
                    offset = index + max(1, len(needle))

        return self._success(
            call.id,
            arguments.query,
            matches,
            files_scanned,
            skipped_files,
            truncated,
        )

    @staticmethod
    def _success(
        call_id: str,
        query: str,
        matches: list[dict[str, object]],
        files_scanned: int,
        skipped_files: int,
        truncated: bool,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=call_id,
            content=json.dumps(
                {
                    "files_scanned": files_scanned,
                    "matches": matches,
                    "query": query,
                    "skipped_files": skipped_files,
                    "truncated": truncated,
                },
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


def _preview(line: str, index: int, match_length: int, limit: int) -> str:
    if len(line) <= limit:
        return line
    start = max(0, index - (limit - match_length) // 2)
    end = min(len(line), start + limit)
    start = max(0, end - limit)
    return line[start:end]
