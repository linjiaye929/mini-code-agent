from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.command.runner import CommandRunner
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import (
    PolicyDecision,
    PolicyRule,
    SessionMode,
    TrustSource,
)
from mini_code_agent.providers.base import FinishReason, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.run_command import RunCommandTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary


def call(code: str) -> ToolCall:
    return ToolCall(
        id="command-1",
        name="run_command",
        arguments={
            "argv": [sys.executable, "-c", code],
            "reason": "Run a bounded verification command.",
        },
    )


def executor_for(
    root: Path,
    *,
    approved: bool,
    allow_ask: bool,
    session_mode: SessionMode = SessionMode.INTERACTIVE,
) -> tuple[GovernedToolExecutor, StaticApprovalHandler]:
    rules = (
        (
            PolicyRule(
                id="ask-command",
                decision=PolicyDecision.ASK,
                rationale="Verification commands require approval.",
                tool_glob="run_command",
                side_effect=SideEffect.EXECUTE,
            ),
        )
        if allow_ask
        else ()
    )
    approval = StaticApprovalHandler(approved=approved)
    executor = GovernedToolExecutor(
        ToolRegistry(
            [
                RunCommandTool(
                    WorkspaceBoundary(root),
                    CommandRunner(),
                )
            ]
        ),
        policy=PolicyEngine(rules),
        approval=approval,
        session_mode=session_mode,
        trust_source=TrustSource.MODEL,
    )
    return executor, approval


@pytest.mark.asyncio
async def test_execute_is_denied_by_default_without_starting_process(
    tmp_path: Path,
) -> None:
    executor, approval = executor_for(tmp_path, approved=True, allow_ask=False)

    result = await executor.execute(
        call("import pathlib; pathlib.Path('denied.txt').write_text('bad')")
    )

    assert json.loads(result.content)["error"]["code"] == "permission_denied"
    assert approval.requests == []
    assert not (tmp_path / "denied.txt").exists()


@pytest.mark.asyncio
async def test_explicit_ask_executes_only_after_approval(tmp_path: Path) -> None:
    executor, approval = executor_for(tmp_path, approved=True, allow_ask=True)

    result = await executor.execute(
        call("import pathlib; pathlib.Path('approved.txt').write_text('ok')")
    )

    assert result.is_error is False
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok"
    assert len(approval.requests) == 1
    preview = approval.requests[0].preview
    assert preview.command is not None
    assert preview.command[:2] == (sys.executable, "-c")
    assert preview.resources == (".",)


@pytest.mark.asyncio
async def test_non_interactive_ask_never_prompts_or_starts_process(
    tmp_path: Path,
) -> None:
    executor, approval = executor_for(
        tmp_path,
        approved=True,
        allow_ask=True,
        session_mode=SessionMode.NON_INTERACTIVE,
    )

    result = await executor.execute(
        call("import pathlib; pathlib.Path('unattended.txt').write_text('bad')")
    )

    assert json.loads(result.content)["error"]["code"] == "permission_denied"
    assert approval.requests == []
    assert not (tmp_path / "unattended.txt").exists()


@pytest.mark.asyncio
async def test_agent_completes_governed_command_round_trip(tmp_path: Path) -> None:
    executor, approval = executor_for(tmp_path, approved=True, allow_ask=True)
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(call("print('verified')"),),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            ModelResponse(
                message=Message.assistant_text("Verification completed."),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )

    result = await AgentRuntime(provider, executor).run(
        user_prompt="Run verification.",
        run_id="governed-command-run",
    )

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_text == "Verification completed."
    assert result.tool_calls == 1
    assert len(approval.requests) == 1
    tool_result = provider.requests[1].messages[-1].tool_results[0]
    body = json.loads(tool_result.content)
    assert tool_result.tool_call_id == "command-1"
    assert body["exit_code"] == 0
    assert "verified" in body["stdout"]
