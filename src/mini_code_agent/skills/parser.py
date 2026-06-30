from __future__ import annotations

# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
from hashlib import sha256
from typing import NamedTuple

import yaml
from pydantic import ValidationError
from yaml.composer import ComposerError
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from mini_code_agent.skills.models import SkillIssueCode, SkillMetadata


class SkillParseError(ValueError):
    def __init__(self, code: SkillIssueCode) -> None:
        self.code = code
        super().__init__("Skill document is invalid.")


class ParsedSkill(NamedTuple):
    metadata: SkillMetadata
    body: str
    sha256: str
    byte_count: int


class _StrictSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: Node | None, index: int) -> Node:
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise ComposerError(None, None, "YAML aliases are not supported.", event.start_mark)
        candidate = super().compose_node(parent, index)
        if candidate is None:
            raise ComposerError(None, None, "YAML node is missing.", None)
        return candidate


def _construct_unique_string_mapping(
    loader: _StrictSafeLoader,
    node: Node,
    deep: bool = False,
) -> dict[str, object]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(None, None, "Expected a mapping.", node.start_mark)
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ConstructorError(None, None, "Mapping keys must be strings.", key_node.start_mark)
        if key in result:
            raise ConstructorError(None, None, "Duplicate mapping key.", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_string_mapping,
)


def parse_skill_document(
    raw: bytes,
    *,
    directory_name: str,
    max_file_bytes: int = 262_144,
    max_frontmatter_bytes: int = 32_768,
    max_body_chars: int = 131_072,
) -> ParsedSkill:
    if (
        max_file_bytes < 1
        or max_frontmatter_bytes < 1
        or max_body_chars < 1
        or len(raw) > max_file_bytes
    ):
        raise SkillParseError(SkillIssueCode.SKILL_TOO_LARGE)
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SkillParseError(SkillIssueCode.INVALID_ENCODING)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SkillParseError(SkillIssueCode.INVALID_ENCODING) from None

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise SkillParseError(SkillIssueCode.INVALID_DOCUMENT)
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing_index is None:
        raise SkillParseError(SkillIssueCode.INVALID_DOCUMENT)

    frontmatter = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :])
    if len(frontmatter.encode("utf-8")) > max_frontmatter_bytes:
        raise SkillParseError(SkillIssueCode.SKILL_TOO_LARGE)
    if len(body) > max_body_chars:
        raise SkillParseError(SkillIssueCode.SKILL_TOO_LARGE)
    if not body.strip():
        raise SkillParseError(SkillIssueCode.INVALID_BODY)

    try:
        # The custom loader subclasses SafeLoader and further rejects aliases/duplicate keys.
        candidate = yaml.load(frontmatter, Loader=_StrictSafeLoader)  # nosec B506
    except yaml.YAMLError:
        raise SkillParseError(SkillIssueCode.INVALID_FRONTMATTER) from None
    if not isinstance(candidate, dict) or not all(isinstance(key, str) for key in candidate):
        raise SkillParseError(SkillIssueCode.INVALID_FRONTMATTER)
    try:
        metadata = SkillMetadata.model_validate(candidate)
    except ValidationError:
        raise SkillParseError(SkillIssueCode.INVALID_METADATA) from None
    if metadata.name != directory_name:
        raise SkillParseError(SkillIssueCode.INVALID_METADATA)

    return ParsedSkill(
        metadata=metadata,
        body=body,
        sha256=sha256(raw).hexdigest(),
        byte_count=len(raw),
    )
