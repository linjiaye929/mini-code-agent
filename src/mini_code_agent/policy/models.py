from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.tools.base import SideEffect

type ResourcePath = Annotated[str, Field(min_length=1, max_length=1024)]
type CommandArgument = Annotated[str, Field(max_length=4096)]


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SessionMode(StrEnum):
    INTERACTIVE = "interactive"
    NON_INTERACTIVE = "non_interactive"


class TrustSource(StrEnum):
    USER = "user"
    PROJECT = "project"
    MODEL = "model"
    EXTENSION = "extension"
    SUBAGENT = "subagent"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    side_effect: SideEffect
    risk: RiskLevel
    resources: tuple[ResourcePath, ...] = Field(default=(), max_length=32)
    command: tuple[CommandArgument, ...] = Field(default=(), max_length=64)
    session_mode: SessionMode
    trust_source: TrustSource


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    decision: PolicyDecision
    rationale: str = Field(min_length=1, max_length=500)
    tool_glob: str = Field(default="*", min_length=1, max_length=128)
    side_effect: SideEffect | None = None
    risk: RiskLevel | None = None
    resource_glob: str | None = Field(default=None, min_length=1, max_length=1024)
    executable_glob: str | None = Field(default=None, min_length=1, max_length=4096)
    session_mode: SessionMode | None = None
    trust_source: TrustSource | None = None


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: PolicyDecision
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    rationale: str = Field(min_length=1, max_length=500)


class ActionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    side_effect: SideEffect
    risk: RiskLevel
    summary: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="No reason provided.", min_length=1, max_length=500)
    resources: tuple[ResourcePath, ...] = Field(default=(), max_length=32)
    command: tuple[CommandArgument, ...] | None = Field(default=None, max_length=64)
    diff: str | None = Field(default=None, max_length=32_768)


class ActionGuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    public_message: str = Field(
        default="Tool execution was not permitted.",
        min_length=1,
        max_length=500,
    )


class ActionGuard(Protocol):
    def evaluate(self, preview: ActionPreview) -> ActionGuardResult: ...


class AllowAllActionGuard:
    def evaluate(self, preview: ActionPreview) -> ActionGuardResult:
        del preview
        return ActionGuardResult(allowed=True)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preview: ActionPreview
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    rationale: str = Field(min_length=1, max_length=500)
