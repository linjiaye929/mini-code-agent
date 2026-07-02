from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.approval import StaticApprovalHandler
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.executor import GovernedToolExecutor
from mini_code_agent.policy.models import SessionMode, TrustSource
from mini_code_agent.subagents.models import SubagentProfile, SubagentStatus
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.workspace.models import MutationResult
from mini_code_agent.worktrees.adoption import (
    AdoptSubagentCandidateTool,
    CandidateAdoptionError,
    CandidateAdoptionService,
    DiscardSubagentCandidateTool,
)
from mini_code_agent.worktrees.finalization import WorktreeFinalizer
from mini_code_agent.worktrees.git import WorktreeGit
from mini_code_agent.worktrees.ledger import MutationLedger
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import (
    AdoptionStatus,
    CandidateState,
    CleanupStatus,
    DiscardStatus,
    SnapshotStatus,
    WorktreeProfile,
)
from mini_code_agent.worktrees.snapshot import CandidateSnapshotter
from mini_code_agent.worktrees.state import WorktreeStateStore


@dataclass(frozen=True, slots=True)
class AdoptionFixture:
    profile: WorktreeProfile
    store: WorktreeStateStore
    git: WorktreeGit
    service: CandidateAdoptionService
    base: dict[str, bytes]
    after: dict[str, bytes]


async def ready_candidate(tmp_path: Path) -> AdoptionFixture:
    discovered_git = shutil.which("git")
    if discovered_git is None:
        pytest.skip("Git is unavailable.")
    repository = tmp_path / "repository"
    state = tmp_path / "state"
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository.mkdir()
    state.mkdir()
    if os.name != "nt":
        state.chmod(0o700)
    _git(repository, "init")
    _git(repository, "config", "user.email", "agent@example.invalid")
    _git(repository, "config", "user.name", "Agent Test")
    (repository / "src").mkdir()
    base = {
        "src/a.py": b"A = 1\n",
        "src/b.py": b"B = 1\n",
    }
    after = {
        "src/a.py": b"A = 2\n",
        "src/b.py": b"B = 2\n",
        "src/new.py": b"NEW = True\n",
    }
    for path, content in base.items():
        repository.joinpath(*path.split("/")).write_bytes(content)
    _git(repository, "add", "--", "src/a.py", "src/b.py")
    _git(repository, "commit", "-m", "initial")
    profile = _profile(
        repository,
        state,
        Path(discovered_git).resolve(strict=True),
    )
    store = WorktreeStateStore(profile)
    git = WorktreeGit(profile)
    manager = WorktreeManager(
        profile,
        git=git,
        store=store,
        id_factory=lambda: "lease-adoption",
    )
    lease = await manager.create_lease(child_id="child-adoption")
    ledger = MutationLedger(max_entries=8)
    for ordinal, path in enumerate(sorted(after)):
        target = lease.worktree_path.joinpath(*path.split("/"))
        target.write_bytes(after[path])
        before = base.get(path)
        mutation = MutationResult(
            path=path,
            created=before is None,
            before_sha256=(hashlib.sha256(before).hexdigest() if before is not None else None),
            after_sha256=hashlib.sha256(after[path]).hexdigest(),
            byte_count=len(after[path]),
            line_count=1,
            diff="bounded",
        )
        call = ToolCall(id=f"write-{ordinal}", name="write_file", arguments={})
        ledger.record(
            call,
            ToolResult(
                tool_call_id=call.id,
                content=json.dumps(mutation.model_dump(mode="json")),
            ),
        )
    finalization = await WorktreeFinalizer(
        snapshotter=CandidateSnapshotter(
            profile,
            store=store,
            blob_reader=git,
        ),
        cleaner=manager,
    ).finalize(
        lease,
        ledger,
        candidate_id="candidate-adoption",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
    )
    assert finalization.snapshot.status is SnapshotStatus.READY
    assert finalization.cleanup.status is CleanupStatus.REMOVED
    return AdoptionFixture(
        profile=profile,
        store=store,
        git=git,
        service=CandidateAdoptionService(profile, store=store, git=git),
        base=base,
        after=after,
    )


@pytest.mark.asyncio
async def test_adoption_preview_is_read_only_and_execute_applies_exact_candidate(
    tmp_path: Path,
) -> None:
    fixture = await ready_candidate(tmp_path)

    tool = AdoptSubagentCandidateTool(fixture.service)
    call = ToolCall(
        id="adopt-1",
        name="adopt_subagent_candidate",
        arguments={
            "candidate_id": "candidate-adoption",
            "reason": "Apply the verified implementation.",
        },
    )
    preview = await tool.preview(call)
    manifest = await fixture.service.preview("candidate-adoption")
    assert await fixture.git.status_porcelain() == b""
    tool_result = await tool.execute(call)
    result_payload = json.loads(tool_result.content)

    assert preview.risk.value == "high"
    assert result_payload["status"] == AdoptionStatus.APPLIED.value
    for path, content in fixture.after.items():
        assert fixture.profile.repository_root.joinpath(*path.split("/")).read_bytes() == content
    assert await fixture.git.changed_paths() == (
        "src/a.py",
        "src/b.py",
        "src/new.py",
    )
    applied = fixture.store.load_candidate(
        CandidateState.APPLIED,
        "candidate-adoption",
    )
    assert applied.manifest_sha256 == manifest.manifest_sha256
    with pytest.raises(CandidateAdoptionError):
        await fixture.service.discard("candidate-adoption")


@pytest.mark.asyncio
async def test_adoption_conflict_performs_zero_candidate_writes_and_returns_ready(
    tmp_path: Path,
) -> None:
    fixture = await ready_candidate(tmp_path)
    user_content = b"A = 99\n"
    (fixture.profile.repository_root / "src" / "a.py").write_bytes(user_content)

    result = await fixture.service.adopt("candidate-adoption")

    assert result.status is AdoptionStatus.CONFLICT
    assert (fixture.profile.repository_root / "src" / "a.py").read_bytes() == user_content
    assert (fixture.profile.repository_root / "src" / "b.py").read_bytes() == fixture.base[
        "src/b.py"
    ]
    fixture.store.load_candidate(CandidateState.READY, "candidate-adoption")


@pytest.mark.asyncio
async def test_partial_apply_failure_rolls_back_in_reverse_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await ready_candidate(tmp_path)
    original_replace = os.replace
    calls = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second-file failure")
        original_replace(source, target)

    monkeypatch.setattr("mini_code_agent.worktrees.adoption.os.replace", fail_second_replace)

    result = await fixture.service.adopt("candidate-adoption")

    assert result.status is AdoptionStatus.APPLY_FAILED_ROLLED_BACK
    for path, content in fixture.base.items():
        assert fixture.profile.repository_root.joinpath(*path.split("/")).read_bytes() == content
    assert await fixture.git.status_porcelain() == b""
    fixture.store.load_candidate(CandidateState.READY, "candidate-adoption")


@pytest.mark.asyncio
async def test_failed_rollback_marks_candidate_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await ready_candidate(tmp_path)
    original_replace = os.replace
    calls = 0

    def fail_apply_and_rollback(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("simulated apply and rollback failure")
        original_replace(source, target)

    monkeypatch.setattr(
        "mini_code_agent.worktrees.adoption.os.replace",
        fail_apply_and_rollback,
    )

    result = await fixture.service.adopt("candidate-adoption")

    assert result.status is AdoptionStatus.APPLY_UNCERTAIN
    fixture.store.load_candidate(CandidateState.UNCERTAIN, "candidate-adoption")
    recovery = (
        fixture.profile.state_root
        / "candidates"
        / "uncertain"
        / "candidate-adoption"
        / "recovery.json"
    )
    assert json.loads(recovery.read_text(encoding="utf-8"))["status"] == "apply_uncertain"


@pytest.mark.asyncio
async def test_recovery_classifies_all_before_and_all_after_states(
    tmp_path: Path,
) -> None:
    before_fixture = await ready_candidate(tmp_path / "before")
    before_fixture.store.transition_candidate(
        "candidate-adoption",
        CandidateState.READY,
        CandidateState.APPLYING,
    )

    before = await before_fixture.service.recover("candidate-adoption")

    assert before.status is AdoptionStatus.RECOVERED_READY
    before_fixture.store.load_candidate(CandidateState.READY, "candidate-adoption")

    after_fixture = await ready_candidate(tmp_path / "after")
    payload = after_fixture.store.load_candidate_payload(
        CandidateState.READY,
        "candidate-adoption",
    )
    after_fixture.store.transition_candidate(
        "candidate-adoption",
        CandidateState.READY,
        CandidateState.APPLYING,
    )
    for item in payload.manifest.files:
        after_fixture.profile.repository_root.joinpath(*item.path.split("/")).write_bytes(
            payload.blobs[item.content_blob_sha256]
        )

    after = await after_fixture.service.recover("candidate-adoption")

    assert after.status is AdoptionStatus.APPLIED
    after_fixture.store.load_candidate(CandidateState.APPLIED, "candidate-adoption")

    mixed_fixture = await ready_candidate(tmp_path / "mixed")
    mixed_payload = mixed_fixture.store.load_candidate_payload(
        CandidateState.READY,
        "candidate-adoption",
    )
    mixed_fixture.store.transition_candidate(
        "candidate-adoption",
        CandidateState.READY,
        CandidateState.APPLYING,
    )
    first = mixed_payload.manifest.files[0]
    mixed_fixture.profile.repository_root.joinpath(*first.path.split("/")).write_bytes(
        mixed_payload.blobs[first.content_blob_sha256]
    )

    mixed = await mixed_fixture.service.recover("candidate-adoption")

    assert mixed.status is AdoptionStatus.APPLY_UNCERTAIN
    mixed_fixture.store.load_candidate(CandidateState.UNCERTAIN, "candidate-adoption")


@pytest.mark.asyncio
async def test_discard_tool_removes_only_ready_candidate(tmp_path: Path) -> None:
    fixture = await ready_candidate(tmp_path)
    adopt_tool = AdoptSubagentCandidateTool(fixture.service)
    discard_tool = DiscardSubagentCandidateTool(fixture.service)
    call = ToolCall(
        id="discard-1",
        name="discard_subagent_candidate",
        arguments={
            "candidate_id": "candidate-adoption",
            "reason": "Discard the unused candidate.",
        },
    )

    preview = await discard_tool.preview(call)
    result = await discard_tool.execute(call)

    assert preview.side_effect.value == "write"
    assert json.loads(result.content)["status"] == DiscardStatus.DISCARDED.value
    assert not (
        fixture.profile.state_root / "candidates" / "discarding" / "candidate-adoption"
    ).exists()
    with pytest.raises(CandidateAdoptionError):
        await fixture.service.preview("candidate-adoption")
    assert adopt_tool.definition.name == "adopt_subagent_candidate"


@pytest.mark.asyncio
async def test_adoption_requires_separate_parent_approval(tmp_path: Path) -> None:
    fixture = await ready_candidate(tmp_path)
    call = ToolCall(
        id="adopt-1",
        name="adopt_subagent_candidate",
        arguments={
            "candidate_id": "candidate-adoption",
            "reason": "Apply only after explicit approval.",
        },
    )
    denied_approval = StaticApprovalHandler(approved=False)
    denied_executor = GovernedToolExecutor(
        ToolRegistry((AdoptSubagentCandidateTool(fixture.service),)),
        policy=PolicyEngine(),
        approval=denied_approval,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )

    denied = await denied_executor.execute(call)

    assert denied.is_error is True
    assert all(
        fixture.profile.repository_root.joinpath(*path.split("/")).read_bytes() == content
        for path, content in fixture.base.items()
    )
    fixture.store.load_candidate(CandidateState.READY, "candidate-adoption")
    assert len(denied_approval.requests) == 1

    approved_approval = StaticApprovalHandler(approved=True)
    approved_executor = GovernedToolExecutor(
        ToolRegistry((AdoptSubagentCandidateTool(fixture.service),)),
        policy=PolicyEngine(),
        approval=approved_approval,
        session_mode=SessionMode.INTERACTIVE,
        trust_source=TrustSource.MODEL,
    )
    approved = await approved_executor.execute(call.model_copy(update={"id": "adopt-2"}))

    assert approved.is_error is False
    assert json.loads(approved.content)["status"] == AdoptionStatus.APPLIED.value
    assert len(approved_approval.requests) == 1


def _profile(
    repository: Path,
    state: Path,
    git_executable: Path,
) -> WorktreeProfile:
    return WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=git_executable,
        allowed_path_prefixes=("src", "tests"),
        implementation_profile=SubagentProfile(
            profile_id="implementation",
            local_name="delegate_implementation",
            description="Implement one bounded task.",
            system_prompt="Change only files required by the task.",
            tool_names=("read_file", "search_text", "write_file", "edit_file"),
            mode="implementation",
            agent_limits=AgentLimits(max_turns=8, max_tool_calls=32),
        ),
    )


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )
