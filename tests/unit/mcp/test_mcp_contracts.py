from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from mini_code_agent.mcp.contracts import (
    schema_sha256,
    verify_server_contract,
    verify_tool_contracts,
)
from mini_code_agent.mcp.models import (
    MCP_PROTOCOL_VERSION,
    McpConnectionError,
    McpConnectionErrorCode,
    McpInitializeSnapshot,
    McpRemoteTool,
    McpServerProfile,
    McpToolGrant,
    McpToolPage,
)
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import SideEffect

PYTHON_EXECUTABLE = str(Path(sys.executable).resolve())


def independent_sha256(value: Mapping[str, JsonValue]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def input_schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }


def output_schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": {"clean": {"type": "boolean"}},
        "required": ["clean"],
        "additionalProperties": False,
    }


def grant_for(
    *,
    remote_name: str = "status",
    local_name: str = "mcp_status",
    description: str = "Read host-reviewed status.",
    side_effect: SideEffect = SideEffect.READ_ONLY,
    risk: RiskLevel = RiskLevel.LOW,
    input_hash: str | None = None,
    output_hash: str | None = None,
) -> McpToolGrant:
    return McpToolGrant(
        remote_name=remote_name,
        local_name=local_name,
        description=description,
        side_effect=side_effect,
        risk=risk,
        input_schema_sha256=input_hash or independent_sha256(input_schema()),
        output_schema_sha256=output_hash,
    )


def profile_for(
    tmp_path: Path,
    *,
    grants: tuple[McpToolGrant, ...] | None = None,
) -> McpServerProfile:
    return McpServerProfile(
        server_id="local-test",
        command=PYTHON_EXECUTABLE,
        args=("-m", "example_server"),
        cwd=tmp_path.resolve(),
        expected_server_name="mini-code-agent-test",
        expected_server_version="1.0.0",
        grants=grants or (grant_for(),),
    )


def initialized_for(
    *,
    protocol_version: str = MCP_PROTOCOL_VERSION,
    server_name: str = "mini-code-agent-test",
    server_version: str = "1.0.0",
    has_tools: bool = True,
    tools_list_changed: bool = False,
) -> McpInitializeSnapshot:
    return McpInitializeSnapshot(
        protocol_version=protocol_version,
        server_name=server_name,
        server_version=server_version,
        has_tools=has_tools,
        tools_list_changed=tools_list_changed,
    )


def remote_for(
    *,
    name: str = "status",
    input_value: Mapping[str, JsonValue] | None = None,
    output_value: Mapping[str, JsonValue] | None = None,
    task_support: str = "forbidden",
) -> McpRemoteTool:
    return McpRemoteTool.model_validate(
        {
            "name": name,
            "input_schema": dict(input_value or input_schema()),
            "output_schema": dict(output_value) if output_value is not None else None,
            "task_support": task_support,
        }
    )


def test_schema_sha256_is_canonical_across_key_order() -> None:
    left: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
    }
    right: dict[str, JsonValue] = {
        "properties": {"x": {"type": "integer"}},
        "type": "object",
    }

    assert schema_sha256(left) == schema_sha256(right)
    assert schema_sha256(left) == independent_sha256(left)


@pytest.mark.parametrize(
    ("schema", "max_bytes"),
    [
        (cast(Mapping[str, JsonValue], True), 65_536),
        (cast(Mapping[str, JsonValue], {"type": "unknown"}), 65_536),
        (cast(Mapping[str, JsonValue], {"const": float("nan")}), 65_536),
        ({"description": "x" * 100}, 16),
    ],
)
def test_schema_sha256_rejects_invalid_or_oversized_schema(
    schema: Mapping[str, JsonValue],
    max_bytes: int,
) -> None:
    with pytest.raises(McpConnectionError) as caught:
        schema_sha256(schema, max_bytes=max_bytes)

    assert caught.value.code is McpConnectionErrorCode.TOOL_SCHEMA_INVALID


def test_schema_sha256_rejects_excessive_depth_and_nodes() -> None:
    deep: dict[str, JsonValue] = {"type": "string"}
    for _ in range(6):
        deep = {"allOf": [deep]}
    with pytest.raises(McpConnectionError):
        schema_sha256(deep, max_depth=4)

    wide: dict[str, JsonValue] = {
        "enum": cast(list[JsonValue], list(range(20))),
    }
    with pytest.raises(McpConnectionError):
        schema_sha256(wide, max_nodes=10)


def test_server_contract_accepts_exact_identity_and_static_tools(tmp_path: Path) -> None:
    verify_server_contract(profile_for(tmp_path), initialized_for())


@pytest.mark.parametrize(
    ("snapshot", "code"),
    [
        (
            initialized_for(protocol_version="2024-11-05"),
            McpConnectionErrorCode.PROTOCOL_MISMATCH,
        ),
        (
            initialized_for(server_name="replacement"),
            McpConnectionErrorCode.IDENTITY_MISMATCH,
        ),
        (
            initialized_for(server_version="2.0.0"),
            McpConnectionErrorCode.IDENTITY_MISMATCH,
        ),
        (
            initialized_for(has_tools=False),
            McpConnectionErrorCode.TOOLS_CAPABILITY_MISSING,
        ),
        (
            initialized_for(tools_list_changed=True),
            McpConnectionErrorCode.DYNAMIC_TOOLS_UNSUPPORTED,
        ),
    ],
)
def test_server_contract_fails_closed(
    tmp_path: Path,
    snapshot: McpInitializeSnapshot,
    code: McpConnectionErrorCode,
) -> None:
    with pytest.raises(McpConnectionError) as caught:
        verify_server_contract(profile_for(tmp_path), snapshot)

    assert caught.value.code is code


def test_verified_definition_uses_host_authority(tmp_path: Path) -> None:
    grant = grant_for(
        description="Host reviewed status.",
        side_effect=SideEffect.NETWORK,
        risk=RiskLevel.HIGH,
    )

    verified = verify_tool_contracts(
        profile_for(tmp_path, grants=(grant,)),
        McpToolPage(tools=(remote_for(),)),
    )

    assert len(verified) == 1
    assert verified[0].grant is grant
    assert verified[0].definition.name == "mcp_status"
    assert verified[0].definition.description == "Host reviewed status."
    assert verified[0].definition.side_effect is SideEffect.NETWORK
    assert verified[0].risk is RiskLevel.HIGH
    assert verified[0].remote_name == "status"


def test_verified_output_schema_requires_exact_grant(tmp_path: Path) -> None:
    schema = output_schema()
    grant = grant_for(output_hash=independent_sha256(schema))

    verified = verify_tool_contracts(
        profile_for(tmp_path, grants=(grant,)),
        McpToolPage(tools=(remote_for(output_value=schema),)),
    )

    assert verified[0].output_schema is not None
    assert schema_sha256(verified[0].output_schema) == independent_sha256(schema)


@pytest.mark.parametrize(
    ("grants", "page", "code"),
    [
        (
            (grant_for(),),
            McpToolPage(tools=()),
            McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
        ),
        (
            (grant_for(),),
            McpToolPage(tools=(remote_for(), remote_for(name="unexpected"))),
            McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
        ),
        (
            (grant_for(),),
            McpToolPage(tools=(remote_for(), remote_for())),
            McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
        ),
        (
            (grant_for(input_hash="b" * 64),),
            McpToolPage(tools=(remote_for(),)),
            McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
        ),
        (
            (grant_for(),),
            McpToolPage(tools=(remote_for(output_value=output_schema()),)),
            McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
        ),
        (
            (grant_for(output_hash=independent_sha256(output_schema())),),
            McpToolPage(tools=(remote_for(),)),
            McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
        ),
        (
            (grant_for(),),
            McpToolPage(tools=(remote_for(task_support="required"),)),
            McpConnectionErrorCode.UNSUPPORTED_SERVER_FEATURE,
        ),
    ],
)
def test_tool_contracts_reject_drift_and_unsupported_tools(
    tmp_path: Path,
    grants: tuple[McpToolGrant, ...],
    page: McpToolPage,
    code: McpConnectionErrorCode,
) -> None:
    with pytest.raises(McpConnectionError) as caught:
        verify_tool_contracts(profile_for(tmp_path, grants=grants), page)

    assert caught.value.code is code


def test_tool_contracts_reject_pagination_and_profile_limits(tmp_path: Path) -> None:
    with pytest.raises(McpConnectionError) as caught:
        verify_tool_contracts(
            profile_for(tmp_path),
            McpToolPage(tools=(remote_for(),), next_cursor="more"),
        )
    assert caught.value.code is McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH

    profile = profile_for(tmp_path).model_copy(
        update={"limits": profile_for(tmp_path).limits.model_copy(update={"max_tools": 1})}
    )
    oversized = McpToolPage(tools=(remote_for(), remote_for(name="other")))
    with pytest.raises(McpConnectionError) as caught:
        verify_tool_contracts(profile, oversized)
    assert caught.value.code is McpConnectionErrorCode.TOOL_LISTING_TOO_LARGE
