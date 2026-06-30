from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import (
    ActionPreview,
    SessionMode,
    TrustSource,
)
from mini_code_agent.tools.base import ToolDefinition


class HookSource(StrEnum):
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"


class HookPhase(StrEnum):
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


class HookDecision(StrEnum):
    CONTINUE = "continue"
    BLOCK = "block"


class HookOutcome(StrEnum):
    CONTINUED = "continued"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class PreToolHookResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HookDecision
    public_reason: str = Field(
        default="Hook allowed the action.",
        min_length=1,
        max_length=500,
    )


class ToolHookContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call: ToolCall
    definition: ToolDefinition
    preview: ActionPreview
    session_mode: SessionMode
    trust_source: TrustSource

    @model_validator(mode="after")
    def validate_tool_identity(self) -> Self:
        if (
            self.call.name != self.definition.name
            or self.preview.tool_name != self.definition.name
            or self.preview.tool_call_id != self.call.id
            or self.preview.side_effect is not self.definition.side_effect
        ):
            raise ValueError("Hook context Tool identity is inconsistent.")
        return self


class PostToolHookContext(ToolHookContext):
    result: ToolResult

    @model_validator(mode="after")
    def validate_result_identity(self) -> Self:
        if self.result.tool_call_id != self.call.id:
            raise ValueError("Hook result identity is inconsistent.")
        return self


class HookAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hook_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source: HookSource
    phase: HookPhase
    outcome: HookOutcome
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    elapsed_ms: int = Field(ge=0, le=30_000)
    failure_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )

    @model_validator(mode="after")
    def validate_failure_code(self) -> Self:
        failed = self.outcome in {HookOutcome.FAILED, HookOutcome.TIMED_OUT}
        if failed != (self.failure_code is not None):
            raise ValueError("Hook failure code does not match its outcome.")
        return self


class HookGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    hook_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
    )
    failure_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )

    @model_validator(mode="after")
    def validate_gate(self) -> Self:
        if self.allowed and (self.hook_id is not None or self.failure_code is not None):
            raise ValueError("Allowed Hook gate cannot identify a blocker.")
        if not self.allowed and self.hook_id is None:
            raise ValueError("Blocked Hook gate requires a Hook identity.")
        return self
