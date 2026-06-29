from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from mini_code_agent.domain.messages import Message


class ContextLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_context_tokens: int = Field(default=32_768, ge=256, le=1_000_000)
    reserved_output_tokens: int = Field(default=4_096, ge=1, le=500_000)
    marker_max_chars: int = Field(default=500, ge=128, le=2_000)

    @model_validator(mode="after")
    def reserve_must_leave_input(self) -> Self:
        if self.reserved_output_tokens >= self.max_context_tokens:
            raise ValueError("reserved output must be below max context")
        return self

    @computed_field
    @property
    def usable_input_tokens(self) -> int:
        return self.max_context_tokens - self.reserved_output_tokens


class ContextWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str
    messages: tuple[Message, ...] = Field(min_length=1)
    estimated_before: int = Field(ge=0, le=2_000_000_000)
    estimated_after: int = Field(ge=0, le=2_000_000_000)
    omitted_messages: int = Field(default=0, ge=0)
    omitted_tool_exchanges: int = Field(default=0, ge=0)
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @computed_field
    @property
    def compacted(self) -> bool:
        return self.omitted_messages > 0
