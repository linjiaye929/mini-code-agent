from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.providers.base import TokenUsage
from mini_code_agent.subagents.models import (
    SubagentBatchResult,
    SubagentChildResult,
    SubagentErrorCode,
    SubagentEvidenceItem,
    SubagentLimits,
    SubagentProfile,
    SubagentStatus,
)


def limits_for(**changes: object) -> SubagentLimits:
    values: dict[str, object] = {
        "max_tasks": 4,
        "max_concurrency": 2,
        "max_task_chars": 4_000,
        "child_timeout_seconds": 120,
        "batch_timeout_seconds": 300,
        "max_summary_chars": 8_000,
        "max_evidence_items": 64,
        "max_result_bytes": 131_072,
    }
    values.update(changes)
    return SubagentLimits.model_validate(values)


def profile_for(
    *,
    tool_names: tuple[str, ...] = ("read_file", "search_text"),
    agent_limits: AgentLimits | None = None,
    limits: SubagentLimits | None = None,
    max_tasks: int | None = None,
    max_concurrency: int | None = None,
) -> SubagentProfile:
    active_limits = limits or limits_for(
        **(
            {
                key: value
                for key, value in {
                    "max_tasks": max_tasks,
                    "max_concurrency": max_concurrency,
                }.items()
                if value is not None
            }
        )
    )
    return SubagentProfile(
        profile_id="review",
        local_name="delegate_analysis",
        description="Run isolated read-only code analysis.",
        system_prompt="Inspect only the assigned task and return a concise summary.",
        tool_names=tool_names,
        agent_limits=agent_limits
        or AgentLimits(
            max_turns=8,
            max_tool_calls=32,
            provider_timeout_seconds=30,
            tool_timeout_seconds=10,
        ),
        limits=active_limits,
    )


def evidence_for(
    *,
    call_id: str = "call-1",
    tool_name: str = "read_file",
) -> SubagentEvidenceItem:
    return SubagentEvidenceItem(
        tool_call_id=call_id,
        tool_name=tool_name,
        is_error=False,
        content_chars=12,
        content_sha256="a" * 64,
    )


def child_for(
    *,
    child_id: str = "child-1",
    ordinal: int = 0,
    status: SubagentStatus = SubagentStatus.COMPLETED,
    stop_reason: StopReason | None = StopReason.COMPLETED,
    error_code: SubagentErrorCode | None = None,
    error_message: str | None = None,
) -> SubagentChildResult:
    return SubagentChildResult(
        child_id=child_id,
        ordinal=ordinal,
        profile_id="review",
        status=status,
        stop_reason=stop_reason,
        turns=1 if stop_reason is not None else 0,
        tool_calls=1 if stop_reason is not None else 0,
        usage=TokenUsage(input_tokens=10, output_tokens=2),
        untrusted_summary="Review complete." if stop_reason is not None else None,
        evidence=(evidence_for(call_id=f"call-{ordinal + 1}"),) if stop_reason is not None else (),
        error_code=error_code,
        error_message=error_message,
        result_sha256="b" * 64,
    )


def test_analysis_profile_is_exact_frozen_and_bounded() -> None:
    profile = profile_for(max_tasks=3, max_concurrency=2)

    assert profile.mode == "analysis"
    assert profile.tool_names == ("read_file", "search_text")
    assert profile.limits.max_tasks == 3
    assert profile.limits.max_concurrency == 2
    with pytest.raises(ValidationError):
        profile.tool_names = ("write_file",)  # type: ignore[misc]


def test_profile_accepts_explicit_implementation_mode() -> None:
    profile = SubagentProfile.model_validate(
        profile_for().model_dump()
        | {
            "profile_id": "implementation",
            "local_name": "delegate_implementation",
            "mode": "implementation",
            "tool_names": ("read_file", "write_file"),
        }
    )

    assert profile.mode == "implementation"


@pytest.mark.parametrize(
    "tool_names",
    [
        (),
        ("read_file", "read_file"),
        ("delegate_analysis",),
        ("delegate_other",),
        ("Invalid-Name",),
    ],
)
def test_analysis_profile_rejects_empty_duplicate_or_recursive_tools(
    tool_names: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        profile_for(tool_names=tool_names)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_tasks": 5},
        {"max_concurrency": 5},
        {"max_tasks": 2, "max_concurrency": 3},
        {"child_timeout_seconds": 601},
        {"batch_timeout_seconds": 901},
        {"child_timeout_seconds": 301, "batch_timeout_seconds": 300},
        {"max_task_chars": 20_001},
        {"max_summary_chars": 32_001},
        {"max_evidence_items": 257},
        {"max_result_bytes": 1_048_577},
    ],
)
def test_limits_reject_invalid_or_inconsistent_values(
    changes: Mapping[str, object],
) -> None:
    with pytest.raises(ValidationError):
        limits_for(**changes)


def test_profile_rejects_agent_budget_larger_than_subagent_contract() -> None:
    with pytest.raises(ValidationError):
        profile_for(
            agent_limits=AgentLimits(max_turns=33, max_tool_calls=32),
        )
    with pytest.raises(ValidationError):
        profile_for(
            agent_limits=AgentLimits(max_turns=8, max_tool_calls=65),
            limits=limits_for(max_evidence_items=64),
        )


def test_profile_snapshots_tool_names_and_rejects_nul_prompt() -> None:
    names = ["read_file", "search_text"]
    profile = SubagentProfile.model_validate(profile_for().model_dump() | {"tool_names": names})
    names.clear()

    assert profile.tool_names == ("read_file", "search_text")
    with pytest.raises(ValidationError):
        SubagentProfile.model_validate(profile.model_dump() | {"system_prompt": "unsafe\0prompt"})


def test_evidence_is_frozen_bounded_metadata_only() -> None:
    evidence = evidence_for()

    assert evidence.content_chars == 12
    assert set(evidence.model_dump()) == {
        "tool_call_id",
        "tool_name",
        "is_error",
        "content_chars",
        "content_sha256",
    }
    with pytest.raises(ValidationError):
        evidence.content_chars = 13  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SubagentEvidenceItem.model_validate(evidence.model_dump() | {"content_sha256": "invalid"})


@pytest.mark.parametrize(
    ("status", "stop_reason", "error_code", "error_message"),
    [
        (SubagentStatus.COMPLETED, None, None, None),
        (SubagentStatus.STOPPED, None, None, None),
        (
            SubagentStatus.TIMED_OUT,
            StopReason.PROVIDER_TIMEOUT,
            SubagentErrorCode.CHILD_TIMEOUT,
            "Subagent timed out.",
        ),
        (SubagentStatus.FAILED, None, None, None),
        (
            SubagentStatus.BATCH_TIMED_OUT,
            None,
            SubagentErrorCode.CHILD_FAILED,
            "wrong code",
        ),
    ],
)
def test_child_result_rejects_inconsistent_status_fields(
    status: SubagentStatus,
    stop_reason: StopReason | None,
    error_code: SubagentErrorCode | None,
    error_message: str | None,
) -> None:
    with pytest.raises(ValidationError):
        child_for(
            status=status,
            stop_reason=stop_reason,
            error_code=error_code,
            error_message=error_message,
        )


def test_batch_result_factory_counts_statuses_and_hashes_projection() -> None:
    children = (
        child_for(ordinal=0),
        child_for(
            child_id="child-2",
            ordinal=1,
            status=SubagentStatus.TIMED_OUT,
            stop_reason=None,
            error_code=SubagentErrorCode.CHILD_TIMEOUT,
            error_message="Subagent timed out.",
        ),
        child_for(
            child_id="child-3",
            ordinal=2,
            status=SubagentStatus.FAILED,
            stop_reason=None,
            error_code=SubagentErrorCode.CHILD_FAILED,
            error_message="Subagent failed.",
        ),
    )

    batch = SubagentBatchResult.from_children(
        profile_id="review",
        children=children,
        duration_ms=10,
    )

    assert batch.completed == 1
    assert batch.stopped == 0
    assert batch.timed_out == 1
    assert batch.failed == 1
    assert len(batch.result_sha256) == 64
    assert batch.children == children


def test_batch_result_rejects_wrong_counts_order_or_profile() -> None:
    valid = SubagentBatchResult.from_children(
        profile_id="review",
        children=(child_for(),),
        duration_ms=10,
    )
    with pytest.raises(ValidationError):
        SubagentBatchResult.model_validate(valid.model_dump() | {"completed": 0})
    with pytest.raises(ValidationError):
        SubagentBatchResult.from_children(
            profile_id="review",
            children=(child_for(ordinal=1),),
            duration_ms=10,
        )
    with pytest.raises(ValidationError):
        SubagentBatchResult.from_children(
            profile_id="other",
            children=(child_for(),),
            duration_ms=10,
        )


def test_result_models_reject_unbounded_summary_evidence_and_ids() -> None:
    with pytest.raises(ValidationError):
        child_for(child_id="x" * 97)
    with pytest.raises(ValidationError):
        SubagentChildResult.model_validate(
            child_for().model_dump() | {"untrusted_summary": "x" * 32_001}
        )
    with pytest.raises(ValidationError):
        SubagentChildResult.model_validate(
            child_for().model_dump()
            | {"evidence": tuple(evidence_for(call_id=f"call-{i}") for i in range(257))}
        )
