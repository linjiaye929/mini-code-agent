import pytest

from mini_code_agent.skills.models import SkillIssueCode
from mini_code_agent.skills.parser import SkillParseError, parse_skill_document


def document(
    frontmatter: str = (
        "name: review-python\n"
        "description: Review Python changes.\n"
        "version: 1.0.0\n"
        "model_invocable: true"
    ),
    body: str = "Inspect tests before reporting findings.",
    *,
    newline: str = "\n",
) -> bytes:
    text = f"---\n{frontmatter}\n---\n{body}\n"
    return text.replace("\n", newline).encode()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_parser_accepts_strict_skill_document(newline: str) -> None:
    parsed = parse_skill_document(document(newline=newline), directory_name="review-python")

    assert parsed.metadata.name == "review-python"
    assert parsed.metadata.model_invocable is True
    assert parsed.body == f"Inspect tests before reporting findings.{newline}"
    assert len(parsed.sha256) == 64
    assert parsed.byte_count == len(document(newline=newline))


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"\xef\xbb\xbf" + document(), SkillIssueCode.INVALID_ENCODING),
        (b"---\nname: \xff\n---\nbody", SkillIssueCode.INVALID_ENCODING),
        (b"name: review-python\nbody", SkillIssueCode.INVALID_DOCUMENT),
        (b"---\nname: review-python\nbody", SkillIssueCode.INVALID_DOCUMENT),
        (
            document(body="   \n\t"),
            SkillIssueCode.INVALID_BODY,
        ),
    ],
)
def test_parser_rejects_invalid_document_boundaries(
    raw: bytes,
    code: SkillIssueCode,
) -> None:
    with pytest.raises(SkillParseError) as caught:
        parse_skill_document(raw, directory_name="review-python")

    assert caught.value.code is code
    assert str(caught.value) == "Skill document is invalid."


@pytest.mark.parametrize(
    "frontmatter",
    [
        (
            "name: review-python\n"
            "name: shadowed\n"
            "description: Review Python.\n"
            "version: 1.0.0"
        ),
        (
            "name: &name review-python\n"
            "description: *name\n"
            "version: 1.0.0"
        ),
        (
            "name: review-python\n"
            "description: !custom Review Python.\n"
            "version: 1.0.0"
        ),
        "- review-python\n- 1.0.0",
        "1: value",
    ],
)
def test_parser_rejects_unsafe_yaml(frontmatter: str) -> None:
    with pytest.raises(SkillParseError) as caught:
        parse_skill_document(document(frontmatter), directory_name="review-python")

    assert caught.value.code is SkillIssueCode.INVALID_FRONTMATTER


def test_parser_rejects_unknown_metadata_and_directory_mismatch() -> None:
    with pytest.raises(SkillParseError) as unknown:
        parse_skill_document(
            document(
                "name: review-python\n"
                "description: Review Python.\n"
                "version: 1.0.0\n"
                "hooks: []"
            ),
            directory_name="review-python",
        )
    assert unknown.value.code is SkillIssueCode.INVALID_METADATA

    with pytest.raises(SkillParseError) as mismatch:
        parse_skill_document(document(), directory_name="different")
    assert mismatch.value.code is SkillIssueCode.INVALID_METADATA


def test_parser_enforces_file_frontmatter_and_body_limits() -> None:
    raw = document()
    with pytest.raises(SkillParseError) as file_limit:
        parse_skill_document(raw, directory_name="review-python", max_file_bytes=len(raw) - 1)
    assert file_limit.value.code is SkillIssueCode.SKILL_TOO_LARGE

    with pytest.raises(SkillParseError) as metadata_limit:
        parse_skill_document(raw, directory_name="review-python", max_frontmatter_bytes=10)
    assert metadata_limit.value.code is SkillIssueCode.SKILL_TOO_LARGE

    with pytest.raises(SkillParseError) as body_limit:
        parse_skill_document(raw, directory_name="review-python", max_body_chars=10)
    assert body_limit.value.code is SkillIssueCode.SKILL_TOO_LARGE
