from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from mini_code_agent.mcp.models import (
    McpConnectionError,
    McpConnectionErrorCode,
    McpInitializeSnapshot,
    McpRemoteTool,
    McpServerProfile,
    McpToolGrant,
    McpToolPage,
)
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class VerifiedMcpTool:
    grant: McpToolGrant
    definition: ToolDefinition
    output_schema: Mapping[str, JsonValue] | None

    @property
    def remote_name(self) -> str:
        return self.grant.remote_name

    @property
    def risk(self) -> RiskLevel:
        return self.grant.risk


def schema_sha256(
    schema: Mapping[str, JsonValue],
    *,
    max_bytes: int = 65_536,
    max_depth: int = 16,
    max_nodes: int = 10_000,
) -> str:
    candidate = cast(object, schema)
    if (
        not 2 <= max_bytes <= 262_144
        or not 1 <= max_depth <= 64
        or not 1 <= max_nodes <= 100_000
        or not isinstance(candidate, Mapping)
    ):
        raise McpConnectionError(McpConnectionErrorCode.TOOL_SCHEMA_INVALID)
    typed_candidate = cast(Mapping[str, JsonValue], candidate)
    try:
        plain = _bounded_plain_json(
            typed_candidate,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        if not isinstance(plain, dict):
            raise TypeError
        Draft202012Validator.check_schema(plain)
        raw = json.dumps(
            plain,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (SchemaError, TypeError, ValueError, OverflowError, RecursionError):
        raise McpConnectionError(McpConnectionErrorCode.TOOL_SCHEMA_INVALID) from None
    if len(raw) > max_bytes:
        raise McpConnectionError(McpConnectionErrorCode.TOOL_SCHEMA_INVALID)
    return hashlib.sha256(raw).hexdigest()


def verify_server_contract(
    profile: McpServerProfile,
    initialized: McpInitializeSnapshot,
) -> None:
    if initialized.protocol_version != profile.expected_protocol_version:
        raise McpConnectionError(McpConnectionErrorCode.PROTOCOL_MISMATCH)
    if (
        initialized.server_name != profile.expected_server_name
        or initialized.server_version != profile.expected_server_version
    ):
        raise McpConnectionError(McpConnectionErrorCode.IDENTITY_MISMATCH)
    if not initialized.has_tools:
        raise McpConnectionError(McpConnectionErrorCode.TOOLS_CAPABILITY_MISSING)
    if initialized.tools_list_changed:
        raise McpConnectionError(McpConnectionErrorCode.DYNAMIC_TOOLS_UNSUPPORTED)


def verify_tool_contracts(
    profile: McpServerProfile,
    page: McpToolPage,
) -> tuple[VerifiedMcpTool, ...]:
    if len(page.tools) > profile.limits.max_tools:
        raise McpConnectionError(McpConnectionErrorCode.TOOL_LISTING_TOO_LARGE)
    if page.next_cursor is not None:
        raise McpConnectionError(McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH)

    observed_names = tuple(item.name for item in page.tools)
    granted_names = tuple(item.remote_name for item in profile.grants)
    if len(observed_names) != len(set(observed_names)) or set(observed_names) != set(granted_names):
        raise McpConnectionError(McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH)

    observed = {item.name: item for item in page.tools}
    verified: list[VerifiedMcpTool] = []
    for grant in sorted(profile.grants, key=lambda item: item.local_name):
        remote = observed[grant.remote_name]
        if remote.task_support == "required":
            raise McpConnectionError(McpConnectionErrorCode.UNSUPPORTED_SERVER_FEATURE)
        _verify_schema_hash(profile, remote.input_schema, grant.input_schema_sha256)
        _verify_output_schema(profile, remote, grant)
        serialized = remote.model_dump(mode="json")
        input_schema = cast(Mapping[str, JsonValue], serialized["input_schema"])
        verified.append(
            VerifiedMcpTool(
                grant=grant,
                definition=ToolDefinition(
                    name=grant.local_name,
                    description=grant.description,
                    input_schema=input_schema,
                    side_effect=grant.side_effect,
                ),
                output_schema=remote.output_schema,
            )
        )
    return tuple(verified)


def _verify_schema_hash(
    profile: McpServerProfile,
    schema: Mapping[str, JsonValue],
    expected_sha256: str,
) -> None:
    observed = schema_sha256(
        schema,
        max_bytes=profile.limits.max_schema_bytes,
        max_depth=profile.limits.max_json_depth,
        max_nodes=profile.limits.max_json_nodes,
    )
    if observed != expected_sha256:
        raise McpConnectionError(McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH)


def _verify_output_schema(
    profile: McpServerProfile,
    remote: McpRemoteTool,
    grant: McpToolGrant,
) -> None:
    if remote.output_schema is None or grant.output_schema_sha256 is None:
        if remote.output_schema is not None or grant.output_schema_sha256 is not None:
            raise McpConnectionError(McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH)
        return
    _verify_schema_hash(
        profile,
        remote.output_schema,
        grant.output_schema_sha256,
    )


def _bounded_plain_json(
    value: object,
    *,
    max_depth: int,
    max_nodes: int,
) -> JsonValue:
    nodes = 0

    def convert(item: object, depth: int) -> JsonValue:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise ValueError
        if item is None or isinstance(item, (bool, int, str)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError
            return item
        if isinstance(item, Mapping):
            mapping = cast(Mapping[object, object], item)
            converted: dict[str, JsonValue] = {}
            for key, nested in mapping.items():
                if not isinstance(key, str) or len(key) > 1024:
                    raise TypeError
                converted[key] = convert(nested, depth + 1)
            return converted
        if isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            sequence = cast(Sequence[object], item)
            return [convert(nested, depth + 1) for nested in sequence]
        raise TypeError

    return convert(value, 1)
