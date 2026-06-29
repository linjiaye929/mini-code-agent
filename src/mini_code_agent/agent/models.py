from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.domain.messages import Message
from mini_code_agent.providers.base import TokenUsage


class StopReason(StrEnum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    MAX_TOOL_CALLS = "max_tool_calls"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_LIMIT = "provider_limit"
    DUPLICATE_TOOL_CALL = "duplicate_tool_call"
    INVALID_RESPONSE = "invalid_response"
    CONTEXT_LIMIT = "context_limit"
    PERSISTENCE_ERROR = "persistence_error"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: int = Field(default=8, ge=1, le=100)
    max_tool_calls: int = Field(default=32, ge=0, le=1000)
    provider_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=600)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    messages: tuple[Message, ...]
    stop_reason: StopReason
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    usage: TokenUsage
    final_text: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.stop_reason is StopReason.COMPLETED
