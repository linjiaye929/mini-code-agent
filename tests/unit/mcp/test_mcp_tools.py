from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import JsonValue

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.mcp.contracts import VerifiedMcpTool, schema_sha256
from mini_code_agent.mcp.models import (
    McpCallError,
    McpCallErrorCode,
    McpCallResult,
    McpLifecycleState,
    McpLimits,
    McpRemoteTool,
    McpServerProfile,
    McpToolGrant,
    McpToolPage,
)
from mini_code_agent.mcp.tools import McpTool, build_mcp_tools
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import SideEffect, ToolDefinition

PYTHON_EXECUTABLE = str(Path(sys.executable).resolve())


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


def verified_for(
    *,
    side_effect: SideEffect = SideEffect.READ_ONLY,
    risk: RiskLevel = RiskLevel.LOW,
    with_output_schema: bool = False,
) -> VerifiedMcpTool:
    grant = McpToolGrant(
        remote_name="status",
        local_name="mcp_status",
        description="Read host-reviewed status.",
        side_effect=side_effect,
        risk=risk,
        input_schema_sha256=schema_sha256(input_schema()),
        output_schema_sha256=(schema_sha256(output_schema()) if with_output_schema else None),
    )
    return VerifiedMcpTool(
        grant=grant,
        definition=ToolDefinition(
            name=grant.local_name,
            description=grant.description,
            input_schema=input_schema(),
            side_effect=side_effect,
        ),
        output_schema=output_schema() if with_output_schema else None,
    )


def profile_for(
    tmp_path: Path,
    verified: VerifiedMcpTool,
    *,
    limits: McpLimits | None = None,
) -> McpServerProfile:
    return McpServerProfile(
        server_id="local-test",
        command=PYTHON_EXECUTABLE,
        args=("-m", "example_server"),
        cwd=tmp_path.resolve(),
        expected_server_name="mini-code-agent-test",
        expected_server_version="1.0.0",
        grants=(verified.grant,),
        limits=limits or McpLimits(),
    )


def call_for(
    *,
    name: str = "mcp_status",
    call_id: str = "call-1",
) -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        arguments={"path": "."},
    )


class StubClient:
    def __init__(
        self,
        profile: McpServerProfile,
        verified: VerifiedMcpTool,
        *,
        result: McpCallResult | None = None,
        error: McpCallError | None = None,
        state: McpLifecycleState = McpLifecycleState.READY,
    ) -> None:
        self.profile = profile
        self.verified_tools = (verified,)
        self.state = state
        self.result = result or McpCallResult(text=("clean",))
        self.error = error
        self.calls: list[tuple[McpToolGrant, Mapping[str, JsonValue]]] = []

    async def call(
        self,
        grant: McpToolGrant,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult:
        self.calls.append((grant, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def tool_for(
    tmp_path: Path,
    *,
    result: McpCallResult | None = None,
    error: McpCallError | None = None,
    side_effect: SideEffect = SideEffect.READ_ONLY,
    risk: RiskLevel = RiskLevel.LOW,
    with_output_schema: bool = False,
    limits: McpLimits | None = None,
) -> tuple[McpTool, StubClient]:
    verified = verified_for(
        side_effect=side_effect,
        risk=risk,
        with_output_schema=with_output_schema,
    )
    profile = profile_for(tmp_path, verified, limits=limits)
    client = StubClient(profile, verified, result=result, error=error)
    return McpTool(client, verified), client


@pytest.mark.asyncio
async def test_preview_uses_granted_authority_and_stable_resource(
    tmp_path: Path,
) -> None:
    tool, _ = tool_for(
        tmp_path,
        side_effect=SideEffect.NETWORK,
        risk=RiskLevel.HIGH,
    )

    preview = await tool.preview(call_for())

    assert tool.definition.name == "mcp_status"
    assert tool.definition.description == "Read host-reviewed status."
    assert preview.side_effect is SideEffect.NETWORK
    assert preview.risk is RiskLevel.HIGH
    assert preview.resources == ("mcp://local-test/tools/status",)
    assert preview.command is None
    assert preview.diff is None


@pytest.mark.asyncio
async def test_execute_routes_exact_grant_and_arguments(tmp_path: Path) -> None:
    tool, client = tool_for(
        tmp_path,
        result=McpCallResult(
            text=("clean", "second"),
            structured_content={"clean": True},
        ),
    )

    result = await tool.execute(call_for())

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload == {
        "content_type": "mcp_tool_result",
        "server_id": "local-test",
        "structured_content": {"clean": True},
        "text": ["clean", "second"],
        "tool": "status",
    }
    assert len(client.calls) == 1
    assert client.calls[0][0].remote_name == "status"
    assert client.calls[0][1] == {"path": "."}


@pytest.mark.asyncio
async def test_execute_rejects_wrong_local_tool_name_without_remote_call(
    tmp_path: Path,
) -> None:
    tool, client = tool_for(tmp_path)

    result = await tool.execute(call_for(name="other"))

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "unknown_tool"
    assert client.calls == []


@pytest.mark.asyncio
async def test_remote_business_error_remains_bounded_corrective_result(
    tmp_path: Path,
) -> None:
    tool, _ = tool_for(
        tmp_path,
        result=McpCallResult(text=("Path is invalid.",), is_error=True),
        with_output_schema=True,
    )

    result = await tool.execute(call_for())

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["text"] == ["Path is invalid."]
    assert "structured_content" not in payload


@pytest.mark.asyncio
async def test_success_with_output_schema_requires_valid_structured_content(
    tmp_path: Path,
) -> None:
    valid, _ = tool_for(
        tmp_path,
        result=McpCallResult(structured_content={"clean": True}),
        with_output_schema=True,
    )
    assert (await valid.execute(call_for())).is_error is False

    missing, _ = tool_for(
        tmp_path,
        result=McpCallResult(text=("clean",)),
        with_output_schema=True,
    )
    missing_result = await missing.execute(call_for())
    assert missing_result.is_error is True
    assert json.loads(missing_result.content)["error"]["code"] == "mcp_tool_result_invalid"

    invalid, _ = tool_for(
        tmp_path,
        result=McpCallResult(structured_content={"clean": "yes"}),
        with_output_schema=True,
    )
    invalid_result = await invalid.execute(call_for())
    assert invalid_result.is_error is True
    assert json.loads(invalid_result.content)["error"]["code"] == "mcp_tool_result_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limits", "result", "expected_code"),
    [
        (
            McpLimits(max_text_blocks=1),
            McpCallResult(text=("one", "two")),
            "mcp_tool_result_too_large",
        ),
        (
            McpLimits(max_text_chars=3),
            McpCallResult(text=("four",)),
            "mcp_tool_result_too_large",
        ),
        (
            McpLimits(max_result_bytes=64),
            McpCallResult(text=("x" * 100,)),
            "mcp_tool_result_too_large",
        ),
        (
            McpLimits(max_json_depth=2),
            McpCallResult(structured_content={"one": {"two": True}}),
            "mcp_tool_result_too_large",
        ),
        (
            McpLimits(max_json_nodes=2),
            McpCallResult(structured_content={"one": 1, "two": 2}),
            "mcp_tool_result_too_large",
        ),
        (
            McpLimits(),
            McpCallResult(structured_content={"number": float("nan")}),
            "mcp_tool_result_invalid",
        ),
    ],
)
async def test_result_limits_fail_without_partial_content(
    tmp_path: Path,
    limits: McpLimits,
    result: McpCallResult,
    expected_code: str,
) -> None:
    tool, _ = tool_for(tmp_path, limits=limits, result=result)

    actual = await tool.execute(call_for())

    assert actual.is_error is True
    payload = json.loads(actual.content)
    assert payload["error"]["code"] == expected_code
    assert "x" * 10 not in actual.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        McpCallErrorCode.NOT_CONNECTED,
        McpCallErrorCode.TIMEOUT,
        McpCallErrorCode.FAILED,
        McpCallErrorCode.COMPLETION_UNKNOWN,
    ],
)
async def test_client_errors_map_to_static_tool_errors(
    tmp_path: Path,
    code: McpCallErrorCode,
) -> None:
    tool, _ = tool_for(tmp_path, error=McpCallError(code))

    result = await tool.execute(call_for())

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"]["code"] == code.value
    assert payload["error"]["message"] == str(McpCallError(code))


def test_build_tools_requires_ready_verified_client(tmp_path: Path) -> None:
    verified = verified_for()
    profile = profile_for(tmp_path, verified)
    ready = StubClient(profile, verified)

    tools = build_mcp_tools(ready)

    assert tuple(tool.definition.name for tool in tools) == ("mcp_status",)

    closed = StubClient(
        profile,
        verified,
        state=McpLifecycleState.CLOSED,
    )
    with pytest.raises(ValueError, match="ready"):
        build_mcp_tools(closed)


def test_remote_snapshot_is_not_needed_to_build_adapter(tmp_path: Path) -> None:
    verified = verified_for()
    profile = profile_for(tmp_path, verified)
    client = StubClient(profile, verified)
    unrelated_page = McpToolPage(
        tools=(McpRemoteTool(name="other", input_schema={"type": "object"}),)
    )

    assert unrelated_page.tools[0].name == "other"
    assert build_mcp_tools(client)[0].definition.name == "mcp_status"
