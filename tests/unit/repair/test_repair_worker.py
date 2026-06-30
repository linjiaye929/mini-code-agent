from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from mini_code_agent.agent.models import AgentResult, StopReason
from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.repair.approval import (
    DenyAllRepairApprovalHandler,
    StaticRepairApprovalHandler,
)
from mini_code_agent.repair.models import (
    RepairLimits,
    RepairPreview,
    RepairTestSummary,
    RepairWorkerRequest,
)
from mini_code_agent.repair.worker import AgentRepairWorker
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestDiagnostic,
    PytestDiagnosticOutcome,
    PytestExecutionStatus,
    PytestReportStatus,
)

SHA = "a" * 64


@dataclass(frozen=True)
class RuntimeCall:
    user_prompt: str
    system_prompt: str
    run_id: str | None


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[RuntimeCall] = []

    async def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        run_id: str | None = None,
    ) -> AgentResult:
        self.calls.append(RuntimeCall(user_prompt, system_prompt, run_id))
        return AgentResult(
            run_id=run_id or "generated",
            messages=(),
            stop_reason=StopReason.COMPLETED,
            turns=1,
            tool_calls=1,
            usage=TokenUsage(),
            final_text="Repair attempt complete.",
        )


@pytest.mark.asyncio
async def test_static_approval_records_explicit_decision() -> None:
    preview = repair_preview()
    approved = StaticRepairApprovalHandler(approved=True)
    denied = DenyAllRepairApprovalHandler()

    assert await approved.approve(preview) is True
    assert await denied.approve(preview) is False
    assert approved.requests == [preview]
    assert denied.requests == [preview]


@pytest.mark.asyncio
async def test_agent_worker_builds_canonical_bounded_attempt() -> None:
    runtime = RecordingRuntime()
    worker = AgentRepairWorker(
        runtime,
        scope_sha256=SHA,
        limits=RepairLimits(max_prompt_chars=8_192),
    )

    result = await worker.run(worker_request(attempt=2))

    assert result.stop_reason is StopReason.COMPLETED
    assert worker.scope_sha256 == SHA
    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call.run_id == "repair-1-attempt-2"
    assert "Do not execute tests or commands." in call.system_prompt
    envelope = json.loads(call.user_prompt)
    assert envelope["attempt"] == 2
    assert envelope["last_test"]["diagnostics"][0]["test_name"] == "test_add"
    assert envelope["editable_paths"] == ["src/calculator.py"]


@pytest.mark.asyncio
async def test_agent_worker_uses_bounded_run_id_for_long_repair_identifier() -> None:
    runtime = RecordingRuntime()
    worker = AgentRepairWorker(runtime, scope_sha256=SHA)
    request = worker_request().model_copy(
        update={"repair_id": "r" * 96},
    )

    await worker.run(request)

    run_id = runtime.calls[0].run_id
    assert run_id is not None
    assert len(run_id) <= 96
    assert run_id.startswith("repair-")


@pytest.mark.asyncio
async def test_agent_worker_rejects_oversized_complete_prompt_before_runtime() -> None:
    runtime = RecordingRuntime()
    worker = AgentRepairWorker(
        runtime,
        scope_sha256=SHA,
        limits=RepairLimits(max_prompt_chars=1_024),
    )
    request = worker_request().model_copy(
        update={"user_prompt": "x" * 900},
    )

    with pytest.raises(ValueError, match="Repair worker prompt exceeds"):
        await worker.run(request)

    assert runtime.calls == []


def repair_preview() -> RepairPreview:
    return RepairPreview(
        repair_id="repair-1",
        test_targets=("tests",),
        editable_paths=("src/calculator.py",),
        scope_sha256=SHA,
        max_attempts=3,
        max_elapsed_seconds=900,
        max_patch_bytes=256 * 1024,
        reason="Fix the test.",
    )


def worker_request(*, attempt: int = 1) -> RepairWorkerRequest:
    return RepairWorkerRequest(
        repair_id="repair-1",
        attempt=attempt,
        max_attempts=3,
        remaining_attempts=4 - attempt,
        user_prompt="Fix addition.",
        system_prompt="Keep the change focused.",
        editable_paths=("src/calculator.py",),
        last_test=failed_summary(),
        remaining_elapsed_ms=60_000,
        remaining_patch_bytes=256 * 1024,
    )


def failed_summary() -> RepairTestSummary:
    return RepairTestSummary(
        status=PytestExecutionStatus.FAILED,
        report_status=PytestReportStatus.COMPLETE,
        counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
        diagnostics=(
            PytestDiagnostic(
                outcome=PytestDiagnosticOutcome.FAILURE,
                test_name="test_add",
                file="tests/test_calculator.py",
                line=4,
                message="assert -1 == 3",
                details="left = -1, right = 3",
            ),
        ),
        failure_sha256=SHA,
    )
