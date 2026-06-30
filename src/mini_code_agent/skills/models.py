from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SkillName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$",
    ),
]
SkillId = Annotated[
    str,
    Field(
        min_length=3,
        max_length=72,
        pattern=r"^(managed|user|project):[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$",
    ),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SkillSource(StrEnum):
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"


class SkillTrust(StrEnum):
    MANAGED = "managed"
    USER = "user"
    UNTRUSTED_PROJECT = "untrusted_project"


_TRUST_BY_SOURCE = {
    SkillSource.MANAGED: SkillTrust.MANAGED,
    SkillSource.USER: SkillTrust.USER,
    SkillSource.PROJECT: SkillTrust.UNTRUSTED_PROJECT,
}


class SkillRoot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    source: SkillSource
    root_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")

    @field_validator("path")
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Skill root path must be absolute.")
        return value


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: SkillName
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(
        pattern=(
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
        )
    )
    model_invocable: bool = True


class SkillDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: SkillId
    name: SkillName
    source: SkillSource
    trust: SkillTrust
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=5, max_length=128)
    model_invocable: bool
    relative_path: str = Field(min_length=10, max_length=96)
    byte_count: int = Field(ge=1, le=262_144)
    sha256: Sha256

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.skill_id != f"{self.source.value}:{self.name}":
            raise ValueError("Skill identity does not match source and name.")
        if self.trust is not _TRUST_BY_SOURCE[self.source]:
            raise ValueError("Skill trust does not match its host-selected source.")
        if self.relative_path != f"{self.name}/SKILL.md":
            raise ValueError("Skill display path does not match its name.")
        SkillMetadata(
            name=self.name,
            description=self.description,
            version=self.version,
            model_invocable=self.model_invocable,
        )
        return self


class SkillIssueCode(StrEnum):
    UNSAFE_ROOT = "unsafe_root"
    ROOT_UNAVAILABLE = "root_unavailable"
    LIMIT_EXCEEDED = "limit_exceeded"
    UNSAFE_ENTRY = "unsafe_entry"
    INVALID_ENCODING = "invalid_encoding"
    INVALID_DOCUMENT = "invalid_document"
    INVALID_FRONTMATTER = "invalid_frontmatter"
    INVALID_METADATA = "invalid_metadata"
    INVALID_BODY = "invalid_body"
    SKILL_TOO_LARGE = "skill_too_large"
    CONFLICT = "conflict"
    UNKNOWN_DISABLED_SKILL = "unknown_disabled_skill"
    UNKNOWN_SKILL = "unknown_skill"
    SKILL_DISABLED = "skill_disabled"
    NOT_MODEL_INVOCABLE = "not_model_invocable"
    SKILL_CHANGED = "skill_changed"
    SKILL_UNAVAILABLE = "skill_unavailable"


class SkillIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    skill_id: SkillId | None = None
    code: SkillIssueCode
    message: str = Field(min_length=1, max_length=300)


class SkillDiscoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: tuple[SkillDescriptor, ...] = Field(default=(), max_length=128)
    issues: tuple[SkillIssue, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def require_unique_skills(self) -> Self:
        identities = tuple(item.skill_id for item in self.skills)
        if len(identities) != len(set(identities)):
            raise ValueError("Discovery report contains duplicate Skill identities.")
        return self


class LoadedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    descriptor: SkillDescriptor
    content_type: Literal["untrusted_markdown"] = "untrusted_markdown"
    content: str = Field(min_length=1, max_length=131_072)


def trust_for_source(source: SkillSource) -> SkillTrust:
    return _TRUST_BY_SOURCE[source]
