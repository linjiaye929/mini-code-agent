from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from pydantic import JsonValue, SecretStr

from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.mcp import (
    McpConnectionApprovalRequest,
    McpConnectionError,
    McpConnectionErrorCode,
    McpLifecycleState,
    McpServerProfile,
    McpStdioClient,
    McpToolGrant,
    build_mcp_tools,
    schema_sha256,
)
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import (
    PolicyDecision,
    PolicyRule,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.providers.base import FinishReason, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"clean": {"type": "boolean"}},
    "required": ["clean"],
    "additionalProperties": False,
}
PYTHON_EXECUTABLE = str(Path(sys.executable).resolve())


class RecordingConnectionApprover:
    def __init__(self, approved: bool = True) -> None:
        self._approved = approved
        self.requests: list[McpConnectionApprovalRequest] = []

    async def approve(self, request: McpConnectionApprovalRequest) -> bool:
        self.requests.append(request)
        return self._approved


def server_executable_for(tmp_path: Path) -> str:
    if os.name == "nt":
        return PYTHON_EXECUTABLE
    launcher = tmp_path / "mcp-python-launcher"
    launcher.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import runpy",
                "import sys",
                "script = sys.argv.pop(1)",
                "sys.argv[0] = script",
                "runpy.run_path(script, run_name='__main__')",
                "",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return str(launcher.resolve())


def profile_for(
    tmp_path: Path,
    *,
    args: tuple[str, ...] = (),
    call_log: Path | None = None,
) -> McpServerProfile:
    environment = {"MCP_TEST_CALL_LOG": SecretStr(str(call_log))} if call_log is not None else {}
    return McpServerProfile(
        server_id="fixture",
        command=server_executable_for(tmp_path),
        args=(str(FIXTURE.resolve()), *args),
        cwd=tmp_path.resolve(),
        environment=environment,
        expected_server_name="mini-code-agent-test",
        expected_server_version="1.0.0",
        grants=(
            McpToolGrant(
                remote_name="status",
                local_name="mcp_status",
                description="Read deterministic fixture status.",
                side_effect=SideEffect.READ_ONLY,
                risk=RiskLevel.LOW,
                input_schema_sha256=schema_sha256(INPUT_SCHEMA),
                output_schema_sha256=schema_sha256(OUTPUT_SCHEMA),
            ),
        ),
    )


def provider_for_call() -> ScriptedProvider:
    return ScriptedProvider(
        (
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="mcp-1",
                            name="mcp_status",
                            arguments={"path": "."},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            ModelResponse(
                message=Message.assistant_text("MCP status checked."),
                finish_reason=FinishReason.STOP,
            ),
        )
    )


def executor_for(
    client: McpStdioClient,
    *,
    policy: PolicyEngine | None = None,
    approval: StaticApprovalHandler | None = None,
) -> GovernedToolExecutor:
    tools = build_mcp_tools(client)
    return GovernedToolExecutor(
        ToolRegistry(tools),
        policy=policy or PolicyEngine(),
        approval=approval or StaticApprovalHandler(approved=False),
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        trust_sources={tool.definition.name: TrustSource.EXTENSION for tool in tools},
    )


@pytest.mark.asyncio
async def test_real_stdio_tool_runs_through_governed_agent(
    tmp_path: Path,
) -> None:
    call_log = tmp_path / "calls.jsonl"
    approver = RecordingConnectionApprover()
    profile = profile_for(tmp_path, call_log=call_log)
    client = McpStdioClient(
        profile,
        approver=approver,
    )

    async with client:
        provider = provider_for_call()
        result = await AgentRuntime(provider, executor_for(client)).run(
            user_prompt="Check the fixture status.",
            run_id="governed-mcp-run",
        )

        assert result.stop_reason is StopReason.COMPLETED
        assert result.tool_calls == 1
        tool_result = provider.requests[1].messages[-1].tool_results[0]
        payload = json.loads(tool_result.content)
        assert payload["server_id"] == "fixture"
        assert payload["tool"] == "status"
        assert payload["structured_content"] == {"clean": True}
        assert payload["content_type"] == "mcp_tool_result"
        assert len(approver.requests) == 1
        assert approver.requests[0].command == (
            profile.command,
            str(FIXTURE.resolve()),
        )
        assert approver.requests[0].environment_keys == ("MCP_TEST_CALL_LOG",)
        assert "calls.jsonl" not in approver.requests[0].model_dump_json()

    assert client.state is McpLifecycleState.CLOSED
    assert json.loads(call_log.read_text(encoding="utf-8")) == {
        "name": "status",
        "path": ".",
    }


@pytest.mark.asyncio
async def test_official_session_can_close_from_a_different_task(
    tmp_path: Path,
) -> None:
    client = McpStdioClient(
        profile_for(tmp_path),
        approver=RecordingConnectionApprover(),
    )
    await client.connect()

    await asyncio.create_task(client.aclose())

    assert client.state is McpLifecycleState.CLOSED


@pytest.mark.asyncio
async def test_extension_policy_deny_prevents_remote_call(tmp_path: Path) -> None:
    call_log = tmp_path / "denied.jsonl"
    client = McpStdioClient(
        profile_for(tmp_path, call_log=call_log),
        approver=RecordingConnectionApprover(),
    )
    async with client:
        provider = provider_for_call()
        result = await AgentRuntime(
            provider,
            executor_for(
                client,
                policy=PolicyEngine(
                    rules=(
                        PolicyRule(
                            id="deny-mcp-extension",
                            decision=PolicyDecision.DENY,
                            rationale="MCP extensions disabled.",
                            trust_source=TrustSource.EXTENSION,
                        ),
                    )
                ),
            ),
        ).run(
            user_prompt="Check status.",
            run_id="denied-mcp-run",
        )

        assert result.stop_reason is StopReason.COMPLETED
        denied = provider.requests[1].messages[-1].tool_results[0]
        assert json.loads(denied.content)["error"]["code"] == "permission_denied"
        assert not call_log.exists()


@pytest.mark.asyncio
async def test_connection_approval_does_not_replace_tool_approval(
    tmp_path: Path,
) -> None:
    call_log = tmp_path / "not-approved.jsonl"
    connection_approver = RecordingConnectionApprover()
    tool_approval = StaticApprovalHandler(approved=False)
    client = McpStdioClient(
        profile_for(tmp_path, call_log=call_log),
        approver=connection_approver,
    )
    async with client:
        provider = provider_for_call()
        result = await AgentRuntime(
            provider,
            executor_for(
                client,
                policy=PolicyEngine(
                    rules=(
                        PolicyRule(
                            id="ask-mcp-extension",
                            decision=PolicyDecision.ASK,
                            rationale="MCP call requires approval.",
                            trust_source=TrustSource.EXTENSION,
                        ),
                    )
                ),
                approval=tool_approval,
            ),
        ).run(
            user_prompt="Check status.",
            run_id="ask-mcp-run",
        )

        assert result.stop_reason is StopReason.COMPLETED
        denied = provider.requests[1].messages[-1].tool_results[0]
        assert json.loads(denied.content)["error"]["code"] == "permission_denied"
        assert len(connection_approver.requests) == 1
        assert len(tool_approval.requests) == 1
        assert not call_log.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("arg", ["--extra-tool", "--drift-schema"])
async def test_unexpected_tool_or_schema_drift_admits_nothing(
    tmp_path: Path,
    arg: str,
) -> None:
    client = McpStdioClient(
        profile_for(tmp_path, args=(arg,)),
        approver=RecordingConnectionApprover(),
    )

    with pytest.raises(McpConnectionError) as caught:
        await client.connect()

    assert caught.value.code is McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH
    assert client.verified_tools == ()
    assert client.state is McpLifecycleState.FAILED
    await client.aclose()
    assert client.state is McpLifecycleState.CLOSED
