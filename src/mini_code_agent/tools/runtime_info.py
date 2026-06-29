from __future__ import annotations

import json
import platform
from typing import ClassVar

from mini_code_agent import __version__
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.tools.base import SideEffect, ToolDefinition


class RuntimeInfoTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="runtime_info",
        description="Return package, Python, and operating-system version information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (self._definition,)

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return self._error(call.id, "unknown_tool", "The requested tool is not registered.")
        if call.arguments:
            return self._error(
                call.id,
                "invalid_arguments",
                "runtime_info does not accept arguments.",
            )
        payload = {
            "package_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        }
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(payload, ensure_ascii=True, sort_keys=True),
        )

    @staticmethod
    def _error(call_id: str, code: str, message: str) -> ToolResult:
        payload = {"error": {"code": code, "message": message}}
        return ToolResult(
            tool_call_id=call_id,
            content=json.dumps(payload, ensure_ascii=True, sort_keys=True),
            is_error=True,
        )
