from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    model_validator,
)

from mini_code_agent.domain.json import (
    FrozenJsonValue,
    freeze_json_mapping,
    thaw_json_mapping,
)


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: Mapping[str, JsonValue]

    @model_validator(mode="after")
    def freeze_arguments(self) -> Self:
        object.__setattr__(
            self,
            "arguments",
            freeze_json_mapping(self.arguments),
        )
        return self

    @field_serializer("arguments")
    def serialize_arguments(
        self,
        arguments: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = cast(Mapping[str, FrozenJsonValue], arguments)
        return thaw_json_mapping(frozen)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ToolCall | ToolResult,
    Field(discriminator="type"),
]
