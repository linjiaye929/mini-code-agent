from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Protocol, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.tools.base import ToolDefinition


class RegisteredTool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...


class _SchemaValidator(Protocol):
    def is_valid(self, instance: object) -> bool: ...


class ToolRegistry:
    def __init__(
        self,
        tools: Iterable[RegisteredTool],
        *,
        max_result_chars: int = 8 * 1024 * 1024,
    ) -> None:
        if not 1 <= max_result_chars <= 16 * 1024 * 1024:
            raise ValueError("max_result_chars must be between 1 and 16777216")
        ordered_tools = tuple(tools)
        entries = tuple((tool, tool.definition) for tool in ordered_tools)
        names = tuple(definition.name for _, definition in entries)
        if len(set(names)) != len(names):
            raise ValueError("Tool definitions must have unique names.")

        validators: dict[str, _SchemaValidator] = {}
        for _, definition in entries:
            schema = definition.model_dump(mode="json")["input_schema"]
            try:
                Draft202012Validator.check_schema(schema)
                validators[definition.name] = cast(
                    _SchemaValidator,
                    Draft202012Validator(schema),
                )
            except SchemaError:
                raise ValueError(f"Tool {definition.name!r} has an invalid JSON Schema.") from None

        self._tools = {definition.name: tool for tool, definition in entries}
        self._validators = validators
        self._definitions = tuple(definition for _, definition in entries)
        self._max_result_chars = max_result_chars

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return self._error(
                call.id,
                "unknown_tool",
                "The requested tool is not registered.",
            )

        arguments = call.model_dump(mode="json")["arguments"]
        if not self._validators[call.name].is_valid(arguments):
            return self._error(
                call.id,
                "invalid_arguments",
                "Tool arguments do not match the registered schema.",
            )

        try:
            candidate = cast(object, await tool.execute(call))
        except Exception:
            return self._error(
                call.id,
                "tool_failed",
                "Tool execution failed.",
            )
        if not isinstance(candidate, ToolResult) or candidate.tool_call_id != call.id:
            return self._error(
                call.id,
                "invalid_tool_result",
                "Tool returned an invalid result.",
            )
        if len(candidate.content) > self._max_result_chars:
            return self._error(
                call.id,
                "tool_result_too_large",
                "Tool result exceeded the configured size limit.",
            )
        return candidate

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
