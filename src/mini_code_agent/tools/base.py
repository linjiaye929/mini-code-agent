from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    model_validator,
)

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.json import (
    FrozenJsonValue,
    freeze_json_mapping,
    thaw_json_mapping,
)


class SideEffect(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    input_schema: Mapping[str, JsonValue]
    side_effect: SideEffect

    @model_validator(mode="after")
    def freeze_input_schema(self) -> Self:
        object.__setattr__(
            self,
            "input_schema",
            freeze_json_mapping(self.input_schema),
        )
        return self

    @field_serializer("input_schema")
    def serialize_input_schema(
        self,
        input_schema: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = cast(Mapping[str, FrozenJsonValue], input_schema)
        return thaw_json_mapping(frozen)


class ToolExecutor(Protocol):
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...
