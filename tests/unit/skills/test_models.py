from pathlib import Path

import pytest
from pydantic import ValidationError

from mini_code_agent.skills.models import (
    LoadedSkill,
    SkillDescriptor,
    SkillDiscoveryReport,
    SkillIssue,
    SkillIssueCode,
    SkillMetadata,
    SkillRoot,
    SkillSource,
    SkillTrust,
)


def descriptor_for(
    *,
    source: SkillSource = SkillSource.PROJECT,
    name: str = "review-python",
) -> SkillDescriptor:
    return SkillDescriptor(
        skill_id=f"{source.value}:{name}",
        name=name,
        source=source,
        trust=(
            SkillTrust.UNTRUSTED_PROJECT
            if source is SkillSource.PROJECT
            else SkillTrust(source.value)
        ),
        description="Review Python changes.",
        version="1.2.0-alpha.1",
        model_invocable=True,
        relative_path=f"{name}/SKILL.md",
        byte_count=128,
        sha256="a" * 64,
    )


def test_descriptor_requires_source_qualified_identity_and_derived_trust() -> None:
    descriptor = descriptor_for()

    assert descriptor.skill_id == "project:review-python"
    assert descriptor.trust is SkillTrust.UNTRUSTED_PROJECT

    with pytest.raises(ValidationError):
        descriptor_for().model_copy(update={"trust": SkillTrust.MANAGED}, deep=True).model_validate(
            descriptor_for().model_dump() | {"trust": "managed"}
        )


@pytest.mark.parametrize("version", ["latest", "01.2.3", "1.2", "1.2.3+build"])
def test_metadata_rejects_unsupported_versions(version: str) -> None:
    with pytest.raises(ValidationError):
        SkillMetadata(
            name="review-python",
            description="Review Python.",
            version=version,
        )


def test_metadata_rejects_unknown_fields_and_invalid_names() -> None:
    with pytest.raises(ValidationError):
        SkillMetadata.model_validate(
            {
                "name": "Review_Python",
                "description": "Review Python.",
                "version": "1.0.0",
                "unexpected": True,
            }
        )


def test_root_requires_absolute_path_and_stable_id(tmp_path: Path) -> None:
    root = SkillRoot(path=tmp_path.resolve(), source=SkillSource.USER, root_id="user-main")
    assert root.path.is_absolute()

    with pytest.raises(ValidationError):
        SkillRoot(path=Path("relative"), source=SkillSource.USER, root_id="user-main")


def test_report_rejects_duplicate_skill_ids() -> None:
    descriptor = descriptor_for()
    with pytest.raises(ValidationError):
        SkillDiscoveryReport(skills=(descriptor, descriptor))


def test_loaded_skill_is_bounded_and_labelled() -> None:
    loaded = LoadedSkill(descriptor=descriptor_for(), content="Inspect tests first.")
    assert loaded.content_type == "untrusted_markdown"

    with pytest.raises(ValidationError):
        LoadedSkill(descriptor=descriptor_for(), content="")


def test_issue_contains_only_bounded_public_fields() -> None:
    issue = SkillIssue(
        root_id="project-main",
        skill_id="project:review-python",
        code=SkillIssueCode.INVALID_FRONTMATTER,
        message="Skill frontmatter is invalid.",
    )
    assert set(issue.model_dump()) == {"root_id", "skill_id", "code", "message"}
