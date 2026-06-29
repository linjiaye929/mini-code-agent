from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.tools.base import SideEffect

type ResourcePath = Annotated[str, Field(min_length=1, max_length=1024)]


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


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    side_effect: SideEffect
    risk: RiskLevel
    resources: tuple[ResourcePath, ...] = Field(default=(), max_length=32)
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
    session_mode: SessionMode | None = None
    trust_source: TrustSource | None = None


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: PolicyDecision
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    rationale: str = Field(min_length=1, max_length=500)
