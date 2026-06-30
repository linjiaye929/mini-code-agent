from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.policy.approval import ApprovalHandler, StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import (
    ApprovalRequest,
    PolicyDecision,
    PolicyRule,
    SessionMode,
    TrustSource,
)
from mini_code_agent.providers.base import FinishReason, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.testing.models import PytestProfile
from mini_code_agent.testing.pytest_runner import PytestRunner
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.run_tests import RunTestsTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary

TRACEBACK_SECRET = "TRACEBACK_SECRET_9281"
STDOUT_SECRET = "STDOUT_SECRET_7319"


class FailingApprovalHandler:
    async def approve(self, request: ApprovalRequest) -> bool:
        del request
        raise RuntimeError("private-approval-failure")


def write_test_project(root: Path, *, import_marker: bool = False) -> None:
    tests = root / "tests"
    tests.mkdir()
    marker_line = (
        "from pathlib import Path\n"
        "Path('test-process-started.txt').write_text('started', encoding='utf-8')\n"
        if import_marker
        else ""
    )
    (tests / "test_sample.py").write_text(
        f"""{marker_line}
def test_passes():
    assert 2 + 2 == 4


def test_failure():
    print("{STDOUT_SECRET}")
    assert False, "{TRACEBACK_SECRET}"
""",
        encoding="utf-8",
    )


def pytest_call() -> ToolCall:
    return ToolCall(
        id="tests-1",
        name="run_tests",
        arguments={
            "targets": ["tests"],
            "reason": "Verify the project test suite.",
        },
    )


def executor_for(
    root: Path,
    *,
    allow_ask: bool,
    approved: bool = True,
    session_mode: SessionMode = SessionMode.INTERACTIVE,
    approval: ApprovalHandler | None = None,
) -> tuple[GovernedToolExecutor, ApprovalHandler]:
    rules = (
        (
            PolicyRule(
                id="ask-pytest",
                decision=PolicyDecision.ASK,
                rationale="Project tests execute repository code.",
                tool_glob="run_tests",
                side_effect=SideEffect.EXECUTE,
            ),
        )
        if allow_ask
        else ()
    )
    active_approval = approval or StaticApprovalHandler(approved=approved)
    workspace = WorkspaceBoundary(root)
    runner = PytestRunner(
        root,
        profile=PytestProfile(
            python_executable=Path(sys.executable).resolve(),
            timeout_seconds=30,
            max_failures=5,
        ),
    )
    executor = GovernedToolExecutor(
        ToolRegistry([RunTestsTool(workspace, runner)]),
        policy=PolicyEngine(rules),
        approval=active_approval,
        session_mode=session_mode,
        trust_source=TrustSource.MODEL,
    )
    return executor, active_approval


@pytest.mark.asyncio
async def test_pytest_is_denied_by_default_without_starting_process(
    tmp_path: Path,
) -> None:
    write_test_project(tmp_path, import_marker=True)
    executor, approval = executor_for(tmp_path, allow_ask=False)

    result = await executor.execute(pytest_call())

    assert json.loads(result.content)["error"]["code"] == "permission_denied"
    assert not (tmp_path / "test-process-started.txt").exists()
    assert isinstance(approval, StaticApprovalHandler)
    assert approval.requests == []


@pytest.mark.asyncio
async def test_approved_real_pytest_returns_structured_bounded_diagnostics(
    tmp_path: Path,
) -> None:
    write_test_project(tmp_path)
    executor, approval = executor_for(tmp_path, allow_ask=True)

    result = await executor.execute(pytest_call())

    assert result.is_error is False
    body = json.loads(result.content)
    assert body["status"] == "failed"
    assert body["report_status"] == "complete"
    assert body["counts"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
    }
    assert body["diagnostics"][0]["test_name"] == "test_failure"
    assert TRACEBACK_SECRET in body["diagnostics"][0]["details"]
    assert STDOUT_SECRET in body["stdout"]
    assert "mini-code-agent-pytest-" not in result.content
    assert not (tmp_path / ".pytest_cache").exists()
    assert isinstance(approval, StaticApprovalHandler)
    assert len(approval.requests) == 1
    preview = approval.requests[0].preview
    assert preview.risk.value == "critical"
    assert preview.resources == ("tests",)
    assert preview.command is not None
    assert "--junitxml=<managed-junit-report.xml>" in preview.command


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "session_mode"),
    [
        (False, SessionMode.INTERACTIVE),
        (True, SessionMode.NON_INTERACTIVE),
    ],
)
async def test_rejected_or_noninteractive_ask_does_not_start_pytest(
    tmp_path: Path,
    approved: bool,
    session_mode: SessionMode,
) -> None:
    write_test_project(tmp_path, import_marker=True)
    executor, approval = executor_for(
        tmp_path,
        allow_ask=True,
        approved=approved,
        session_mode=session_mode,
    )

    result = await executor.execute(pytest_call())

    assert json.loads(result.content)["error"]["code"] == "permission_denied"
    assert not (tmp_path / "test-process-started.txt").exists()
    assert isinstance(approval, StaticApprovalHandler)
    expected_requests = 1 if session_mode is SessionMode.INTERACTIVE else 0
    assert len(approval.requests) == expected_requests


@pytest.mark.asyncio
async def test_approval_failure_does_not_start_pytest(tmp_path: Path) -> None:
    write_test_project(tmp_path, import_marker=True)
    executor, _ = executor_for(
        tmp_path,
        allow_ask=True,
        approval=FailingApprovalHandler(),
    )

    result = await executor.execute(pytest_call())

    assert json.loads(result.content)["error"]["code"] == "approval_failed"
    assert "private-approval-failure" not in result.content
    assert not (tmp_path / "test-process-started.txt").exists()


@pytest.mark.asyncio
async def test_agent_receives_diagnostics_without_persisting_test_payload(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_test_project(workspace)
    database = tmp_path / "state.db"
    executor, approval = executor_for(workspace, allow_ask=True)
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(pytest_call(),),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            ModelResponse(
                message=Message.assistant_text("Tests inspected."),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )

    with SqliteSessionTraceStore(database) as store:
        store.create_session("pytest-session")
        result = await AgentRuntime(
            provider,
            executor,
            journal=store.journal("pytest-session"),
        ).run(
            user_prompt="Run project tests.",
            run_id="pytest-agent-run",
        )
        records = store.read_trace("pytest-session", limit=20)
        verification = store.verify_trace("pytest-session")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    body = json.loads(tool_result.content)
    persisted = database.read_bytes()
    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_text == "Tests inspected."
    assert body["status"] == "failed"
    assert TRACEBACK_SECRET in tool_result.content
    assert STDOUT_SECRET in tool_result.content
    assert TRACEBACK_SECRET.encode() not in persisted
    assert STDOUT_SECRET.encode() not in persisted
    assert tuple(record.event.type for record in records) == (
        "run_started",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "model_started",
        "model_completed",
        "run_stopped",
    )
    assert verification.event_count == 8
    assert isinstance(approval, StaticApprovalHandler)
    assert len(approval.requests) == 1
