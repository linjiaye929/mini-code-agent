from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mini_code_agent.domain.content import ToolCall, ToolResult


class SideEffect(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, JsonValue]
    side_effect: SideEffect


class ToolExecutor(Protocol):
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...
