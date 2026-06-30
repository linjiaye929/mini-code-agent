from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.checkpoint.workspace import FilesystemWorkspaceState
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.git.client import GitClient
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import SessionMode, TrustSource
from mini_code_agent.providers.base import FinishReason, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.repair.approval import StaticRepairApprovalHandler
from mini_code_agent.repair.models import RepairRequest, RepairStopReason
from mini_code_agent.repair.runtime import RepairRuntime
from mini_code_agent.repair.scope import RepairActionGuard, RepairScope
from mini_code_agent.repair.worker import AgentRepairWorker
from mini_code_agent.testing.models import PytestProfile
from mini_code_agent.testing.pytest_runner import PytestRunner
from mini_code_agent.tools.edit_file import EditFileTool
from mini_code_agent.tools.read_file import ReadFileTool
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.workspace.boundary import WorkspaceBoundary

BROKEN_SOURCE = """def add(left: int, right: int) -> int:
    return left - right
"""
FIXED_SOURCE = """def add(left: int, right: int) -> int:
    return left + right
"""
TEST_SOURCE = """from runpy import run_path

add = run_path("src/calculator.py")["add"]


def test_adds_two_numbers():
    assert add(1, 2) == 3
"""


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def create_repository(tmp_path: Path, *, mutating_test: bool = False) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "calculator.py").write_bytes(BROKEN_SOURCE.encode("utf-8"))
    test_source = TEST_SOURCE
    if mutating_test:
        test_source = (
            "from pathlib import Path\n"
            "Path('src/calculator.py').write_text('changed by test\\n', encoding='utf-8')\n"
            f"{TEST_SOURCE}"
        )
    (root / "tests" / "test_calculator.py").write_bytes(test_source.encode("utf-8"))
    (root / "README.md").write_bytes(b"project\n")
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "add", "--", "src/calculator.py", "tests/test_calculator.py", "README.md")
    git(root, "commit", "-qm", "initial")
    return root


def response_with(call: ToolCall) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=(call,),
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


def final_response() -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text("One repair attempt completed."),
        finish_reason=FinishReason.STOP,
    )


def repair_request() -> RepairRequest:
    return RepairRequest(
        repair_id="repair-1",
        user_prompt="Fix the arithmetic regression.",
        test_targets=("tests",),
        editable_paths=("src/calculator.py",),
        reason="Repair the failing addition test.",
    )


def pytest_runner(root: Path) -> PytestRunner:
    return PytestRunner(
        root,
        profile=PytestProfile(
            python_executable=Path(sys.executable),
            default_targets=("tests",),
            timeout_seconds=30,
            max_failures=5,
        ),
    )


def worker_executor(
    workspace: WorkspaceBoundary,
    scope: RepairScope,
) -> tuple[GovernedToolExecutor, StaticApprovalHandler]:
    approval = StaticApprovalHandler(approved=True)
    executor = GovernedToolExecutor(
        ToolRegistry([ReadFileTool(workspace), EditFileTool(workspace)]),
        policy=PolicyEngine(),
        approval=approval,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
        guard=RepairActionGuard(scope),
    )
    return executor, approval


@pytest.mark.asyncio
async def test_real_agent_repairs_one_tracked_file_and_persists_both_traces(
    tmp_path: Path,
) -> None:
    root = create_repository(tmp_path)
    database = tmp_path / "state.db"
    workspace = WorkspaceBoundary(root)
    scope = RepairScope.create(workspace, ("src/calculator.py",))
    executor, write_approval = worker_executor(workspace, scope)
    source_sha256 = hashlib.sha256(BROKEN_SOURCE.encode("utf-8")).hexdigest()
    provider = ScriptedProvider(
        [
            response_with(
                ToolCall(
                    id="read-1",
                    name="read_file",
                    arguments={"path": "src/calculator.py"},
                )
            ),
            response_with(
                ToolCall(
                    id="edit-1",
                    name="edit_file",
                    arguments={
                        "path": "src/calculator.py",
                        "old_text": "return left - right",
                        "new_text": "return left + right",
                        "expected_sha256": source_sha256,
                        "reason": "Correct addition implementation.",
                    },
                )
            ),
            final_response(),
        ]
    )

    with SqliteSessionTraceStore(database) as store:
        store.create_session("worker-session")
        agent = AgentRuntime(
            provider,
            executor,
            journal=store.journal("worker-session"),
            checkpoints=store.checkpoints("worker-session"),
            workspace=FilesystemWorkspaceState(root),
        )
        result = await RepairRuntime(
            workspace,
            GitClient(root),
            pytest_runner(root),
            AgentRepairWorker(agent, scope_sha256=scope.sha256),
            StaticRepairApprovalHandler(approved=True),
            journal=store.repair_journal(),
        ).run(repair_request())

        agent_verification = store.verify_trace("worker-session")
        repair_verification = store.verify_repair_trace("repair-1")
        checkpoints = store.list_checkpoints("worker-session")

    status = git(root, "status", "--porcelain=v1").stdout
    patch = git(root, "diff", "--", "src/calculator.py").stdout
    with closing(sqlite3.connect(database)) as connection:
        repair_payloads = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT payload_json FROM repair_events ORDER BY sequence"
            )
        )

    assert result.stop_reason is RepairStopReason.REPAIRED
    assert result.succeeded is True
    assert len(result.attempts) == 1
    assert (root / "src" / "calculator.py").read_text(encoding="utf-8") == FIXED_SOURCE
    assert status.strip() == "M src/calculator.py"
    assert "return left + right" in patch
    assert len(write_approval.requests) == 1
    assert agent_verification.event_count > 0
    assert repair_verification.event_count == 5
    assert len(checkpoints) == 3
    assert "return left + right" not in repair_payloads
    assert "AssertionError" not in repair_payloads


@pytest.mark.asyncio
async def test_out_of_scope_write_is_denied_before_approval_and_disk_mutation(
    tmp_path: Path,
) -> None:
    root = create_repository(tmp_path)
    workspace = WorkspaceBoundary(root)
    scope = RepairScope.create(workspace, ("src/calculator.py",))
    executor, write_approval = worker_executor(workspace, scope)
    readme = (root / "README.md").read_text(encoding="utf-8")
    readme_sha256 = hashlib.sha256(readme.encode("utf-8")).hexdigest()
    provider = ScriptedProvider(
        [
            response_with(
                ToolCall(
                    id="edit-outside",
                    name="edit_file",
                    arguments={
                        "path": "README.md",
                        "old_text": "project",
                        "new_text": "changed",
                        "expected_sha256": readme_sha256,
                        "reason": "Attempt an out-of-scope change.",
                    },
                )
            ),
            final_response(),
        ]
    )
    agent = AgentRuntime(provider, executor)

    result = await RepairRuntime(
        workspace,
        GitClient(root),
        pytest_runner(root),
        AgentRepairWorker(agent, scope_sha256=scope.sha256),
        StaticRepairApprovalHandler(approved=True),
        allow_volatile=True,
    ).run(repair_request())

    assert result.stop_reason is RepairStopReason.NO_PROGRESS
    assert (root / "README.md").read_text(encoding="utf-8") == readme
    assert write_approval.requests == []
    denied = provider.requests[1].messages[-1].tool_results[0]
    assert denied.is_error is True
    assert "permission_denied" in denied.content


@pytest.mark.asyncio
async def test_dirty_repository_blocks_provider_and_pytest_process(
    tmp_path: Path,
) -> None:
    root = create_repository(tmp_path)
    marker = root / "pytest-started.txt"
    test_path = root / "tests" / "test_calculator.py"
    test_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('started')\n{TEST_SOURCE}",
        encoding="utf-8",
    )
    provider = ScriptedProvider([final_response()])
    workspace = WorkspaceBoundary(root)
    scope = RepairScope.create(workspace, ("src/calculator.py",))
    executor, _ = worker_executor(workspace, scope)

    result = await RepairRuntime(
        workspace,
        GitClient(root),
        pytest_runner(root),
        AgentRepairWorker(AgentRuntime(provider, executor), scope_sha256=scope.sha256),
        StaticRepairApprovalHandler(approved=True),
        allow_volatile=True,
    ).run(repair_request())

    assert result.stop_reason is RepairStopReason.DIRTY_REPOSITORY
    assert provider.requests == []
    assert not marker.exists()


@pytest.mark.asyncio
async def test_baseline_test_mutation_stops_before_provider(
    tmp_path: Path,
) -> None:
    root = create_repository(tmp_path, mutating_test=True)
    provider = ScriptedProvider([final_response()])
    workspace = WorkspaceBoundary(root)
    scope = RepairScope.create(workspace, ("src/calculator.py",))
    executor, _ = worker_executor(workspace, scope)

    result = await RepairRuntime(
        workspace,
        GitClient(root),
        pytest_runner(root),
        AgentRepairWorker(AgentRuntime(provider, executor), scope_sha256=scope.sha256),
        StaticRepairApprovalHandler(approved=True),
        allow_volatile=True,
    ).run(repair_request())

    assert result.stop_reason is RepairStopReason.TEST_MUTATED_REPOSITORY
    assert provider.requests == []
