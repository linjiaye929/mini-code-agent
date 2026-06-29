from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.tools.base import ToolDefinition


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALL = "tool_call"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_calling: bool = True
    streaming: bool = True
    parallel_tool_calls: bool = False
    usage: bool = True


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    system_prompt: str
    messages: tuple[Message, ...] = Field(min_length=1)
    tools: tuple[ToolDefinition, ...] = ()


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: Message
    finish_reason: FinishReason
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider_request_id: str | None = None

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if self.message.role is not MessageRole.ASSISTANT:
            raise ValueError("provider response message must have assistant role")
        if self.finish_reason is FinishReason.TOOL_CALL and not self.message.tool_calls:
            raise ValueError("tool_call finish reason requires at least one ToolCall")
        if self.message.tool_calls and self.finish_reason is not FinishReason.TOOL_CALL:
            raise ValueError("ToolCall requires tool_call finish reason")
        return self


class TextDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call_delta"] = "tool_call_delta"
    index: int = Field(ge=0)
    partial_json: str


class ResponseCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["response_completed"] = "response_completed"
    response: ModelResponse


ProviderStreamEvent = Annotated[
    TextDelta | ToolCallDelta | ResponseCompleted,
    Field(discriminator="type"),
]


class ProviderErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVER = "server"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: ProviderErrorCode,
        public_message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


class ModelProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderStreamEvent]: ...
