from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.domain.content import (
    ContentBlock,
    TextBlock,
    ToolCall,
    ToolResult,
)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: tuple[ContentBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_role_content(self) -> Self:
        if self.role is MessageRole.USER and any(
            isinstance(block, ToolCall) for block in self.content
        ):
            raise ValueError("user message cannot contain ToolCall")
        if self.role is MessageRole.ASSISTANT and any(
            isinstance(block, ToolResult) for block in self.content
        ):
            raise ValueError("assistant message cannot contain ToolResult")
        return self

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(block for block in self.content if isinstance(block, ToolCall))

    @property
    def tool_results(self) -> tuple[ToolResult, ...]:
        return tuple(block for block in self.content if isinstance(block, ToolResult))

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    @classmethod
    def user_text(cls, text: str) -> Self:
        return cls(role=MessageRole.USER, content=(TextBlock(text=text),))

    @classmethod
    def assistant_text(cls, text: str) -> Self:
        return cls(role=MessageRole.ASSISTANT, content=(TextBlock(text=text),))
