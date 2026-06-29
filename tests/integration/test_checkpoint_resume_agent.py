from __future__ import annotations

from pathlib import Path

import pytest

from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.checkpoint.fingerprint import tool_contract_sha256
from mini_code_agent.checkpoint.models import (
    CheckpointDraft,
    CheckpointSnapshot,
    CheckpointWriter,
    ResumeCompatibility,
    ResumePolicy,
)
from mini_code_agent.checkpoint.workspace import FilesystemWorkspaceState
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.persistence.errors import PersistenceError, PersistenceErrorCode
from mini_code_agent.persistence.models import RunStatus
from mini_code_agent.persistence.store import SqliteSessionTraceStore
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import SessionMode, TrustSource
from mini_code_agent.providers.base import FinishReason, ModelRequest, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.runtime_info import RuntimeInfoTool
from mini_code_agent.tools.write_file import WriteFileTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary


class ProcessCrash(BaseException):
    pass


class CrashProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__([])

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        raise ProcessCrash


class FailSecondCheckpoint:
    def __init__(self, delegate: CheckpointWriter) -> None:
        self._delegate = delegate
        self.calls = 0

    def save(self, draft: CheckpointDraft) -> CheckpointSnapshot:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("simulated process-boundary failure")
        return self._delegate.save(draft)


def final_response() -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text("done"),
        finish_reason=FinishReason.STOP,
    )


@pytest.mark.asyncio
async def test_reopen_claim_and_resume_after_provider_process_crash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_state = FilesystemWorkspaceState(workspace)
    tools = RuntimeInfoTool()
    crash_provider = CrashProvider()

    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        with pytest.raises(ProcessCrash):
            await AgentRuntime(
                crash_provider,
                tools,
                journal=store.journal("session-1"),
                checkpoints=store.checkpoints("session-1"),
                workspace=workspace_state,
            ).run(
                user_prompt="inspect",
                system_prompt="be precise",
                run_id="run-1",
            )
        saved = store.latest_checkpoint("session-1")

    with SqliteSessionTraceStore(database) as reopened:
        plan = reopened.analyze_resume(
            "session-1",
            saved.checkpoint_id,
            compatibility=ResumeCompatibility(
                tool_contract_sha256=tool_contract_sha256(tools.definitions),
                workspace_sha256=workspace_state.current_sha256(),
            ),
            policy=ResumePolicy(allow_model_retry=True),
        )
        state = reopened.claim_resume(
            plan,
            resumed_run_id="run-2",
            max_turns=8,
            compatibility=ResumeCompatibility(
                tool_contract_sha256=tool_contract_sha256(tools.definitions),
                workspace_sha256=workspace_state.current_sha256(),
            ),
            policy=ResumePolicy(allow_model_retry=True),
        )
        provider = ScriptedProvider([final_response()])
        result = await AgentRuntime(
            provider,
            tools,
            journal=reopened.journal("session-1"),
            checkpoints=reopened.checkpoints("session-1"),
            workspace=workspace_state,
        ).resume(state)

        assert result.stop_reason is StopReason.COMPLETED
        assert provider.requests[0].request_id == "run-2:1"
        assert reopened.get_run("session-1", "run-1").stop_reason is StopReason.INTERRUPTED
        assert reopened.get_run("session-1", "run-2").status is RunStatus.COMPLETED
        assert reopened.verify_trace("session-1").event_count == 9


@pytest.mark.asyncio
async def test_real_governed_write_blocks_resume_after_checkpoint_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_state = FilesystemWorkspaceState(workspace)
    approval = StaticApprovalHandler(approved=True)
    tools = GovernedToolExecutor(
        ToolRegistry([WriteFileTool(WorkspaceBoundary(workspace))]),
        policy=PolicyEngine(),
        approval=approval,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="write-1",
                            name="write_file",
                            arguments={
                                "path": "result.txt",
                                "content": "written once\n",
                                "reason": "Create the result.",
                            },
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            )
        ]
    )

    with SqliteSessionTraceStore(database) as store:
        store.create_session("session-1")
        writer = FailSecondCheckpoint(store.checkpoints("session-1"))
        result = await AgentRuntime(
            provider,
            tools,
            journal=store.journal("session-1"),
            checkpoints=writer,
            workspace=workspace_state,
        ).run(user_prompt="write the result", run_id="run-1")
        saved = store.latest_checkpoint("session-1")

        assert result.stop_reason is StopReason.PERSISTENCE_ERROR
        assert writer.calls == 2
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "written once\n"
        assert len(approval.requests) == 1
        with pytest.raises(PersistenceError) as captured:
            store.analyze_resume(
                "session-1",
                saved.checkpoint_id,
                compatibility=ResumeCompatibility(
                    tool_contract_sha256=saved.tool_contract_sha256,
                    workspace_sha256=saved.workspace_sha256,
                ),
                policy=ResumePolicy(
                    allow_model_retry=True,
                    allow_read_only_retry=True,
                ),
            )

    assert captured.value.code is PersistenceErrorCode.INDETERMINATE_SIDE_EFFECT
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "written once\n"
