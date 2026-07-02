from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.subagents.models import SubagentProfile, SubagentStatus
from mini_code_agent.worktrees.models import (
    CandidateDisposition,
    CandidateFile,
    CandidateManifest,
    CandidateOperation,
    GitIndexEntry,
    MutationLedgerEntry,
    WorktreeLimits,
    WorktreeProfile,
)


def implementation_profile() -> SubagentProfile:
    return SubagentProfile(
        profile_id="implementation",
        local_name="delegate_implementation",
        description="Implement one bounded task in an isolated worktree.",
        system_prompt="Change only files required by the assigned task.",
        tool_names=("read_file", "search_text", "write_file", "edit_file"),
        mode="implementation",
        agent_limits=AgentLimits(max_turns=8, max_tool_calls=32),
    )


def limits_for(**changes: object) -> WorktreeLimits:
    values: dict[str, object] = {
        "max_active_leases": 2,
        "max_tracked_files": 10_000,
        "max_tracked_bytes": 256 * 1024 * 1024,
        "max_tracked_depth": 32,
        "max_candidate_files": 32,
        "max_candidate_after_bytes": 2 * 1024 * 1024,
        "max_file_bytes": 1024 * 1024,
        "max_path_chars": 1024,
        "max_diff_chars": 32_768,
        "cleanup_timeout_seconds": 30,
    }
    values.update(changes)
    return WorktreeLimits.model_validate(values)


def profile_for(
    tmp_path: Path,
    *,
    allowed_path_prefixes: tuple[str, ...] = ("src", "tests/unit"),
    limits: WorktreeLimits | None = None,
) -> WorktreeProfile:
    repository = tmp_path / "repository"
    state = tmp_path / "state"
    executable = tmp_path / ("git.exe" if os.name == "nt" else "git")
    repository.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    executable.touch(exist_ok=True)
    if os.name != "nt":
        state.chmod(0o700)
        executable.chmod(0o700)
    return WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=executable,
        allowed_path_prefixes=allowed_path_prefixes,
        implementation_profile=implementation_profile(),
        limits=limits or limits_for(),
    )


def test_worktree_profile_resolves_and_freezes_host_configuration(tmp_path: Path) -> None:
    profile = profile_for(
        tmp_path,
        allowed_path_prefixes=("src/", "tests/unit/"),
    )

    assert profile.repository_root == (tmp_path / "repository").resolve()
    assert profile.state_root == (tmp_path / "state").resolve()
    assert (
        profile.git_executable == (tmp_path / ("git.exe" if os.name == "nt" else "git")).resolve()
    )
    assert profile.allowed_path_prefixes == ("src", "tests/unit")
    assert profile.implementation_profile.mode == "implementation"
    with pytest.raises(ValidationError):
        profile.allowed_path_prefixes = ("docs",)  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"max_active_leases": 5},
        {"max_tracked_files": 20_001},
        {"max_tracked_bytes": 512 * 1024 * 1024 + 1},
        {"max_tracked_depth": 65},
        {"max_candidate_files": 129},
        {"max_candidate_after_bytes": 8 * 1024 * 1024 + 1},
        {"max_file_bytes": 2 * 1024 * 1024 + 1},
        {"max_path_chars": 1025},
        {"max_diff_chars": 65_537},
        {"cleanup_timeout_seconds": 301},
        {"max_file_bytes": 1024, "max_candidate_after_bytes": 512},
    ],
)
def test_worktree_limits_reject_hard_ceiling_or_relationship_violations(
    changes: Mapping[str, object],
) -> None:
    with pytest.raises(ValidationError):
        limits_for(**changes)


@pytest.mark.parametrize(
    "prefixes",
    [
        (),
        ("src", "src"),
        ("src", "SRC"),
        ("/absolute",),
        ("../outside",),
        ("src\\package",),
        ("src//package",),
        ("src/./package",),
        ("src/../tests",),
        (".git",),
        ("src/.git/config",),
        ("src\0unsafe",),
    ],
)
def test_worktree_profile_rejects_ambiguous_or_unsafe_prefixes(
    tmp_path: Path,
    prefixes: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        profile_for(tmp_path, allowed_path_prefixes=prefixes)


def test_worktree_profile_requires_separate_existing_absolute_paths(
    tmp_path: Path,
) -> None:
    valid = profile_for(tmp_path)

    with pytest.raises(ValidationError):
        WorktreeProfile.model_validate(
            valid.model_dump() | {"state_root": valid.repository_root / "state"}
        )
    with pytest.raises(ValidationError):
        WorktreeProfile.model_validate(
            valid.model_dump() | {"repository_root": Path("relative-repository")}
        )
    with pytest.raises(ValidationError):
        WorktreeProfile.model_validate(
            valid.model_dump() | {"git_executable": tmp_path / "missing-git"}
        )


def test_worktree_profile_requires_exact_implementation_subagent(
    tmp_path: Path,
) -> None:
    valid = profile_for(tmp_path)
    analysis_profile = valid.implementation_profile.model_copy(update={"mode": "analysis"})

    with pytest.raises(ValidationError):
        WorktreeProfile.model_validate(
            valid.model_dump() | {"implementation_profile": analysis_profile}
        )


def test_index_and_ledger_records_are_canonical_and_bounded() -> None:
    entry = GitIndexEntry(
        path="src/app.py",
        mode="100644",
        object_id="a" * 40,
        byte_count=12,
        sha256="b" * 64,
    )
    ledger = MutationLedgerEntry(
        ordinal=0,
        tool_call_id="call-1",
        tool_name="write_file",
        path="src/app.py",
        created=False,
        before_sha256="b" * 64,
        after_sha256="c" * 64,
        byte_count=13,
        line_count=1,
    )

    assert entry.stage == 0
    assert ledger.ordinal == 0
    with pytest.raises(ValidationError):
        GitIndexEntry.model_validate(entry.model_dump() | {"mode": "120000"})
    with pytest.raises(ValidationError):
        MutationLedgerEntry.model_validate(ledger.model_dump() | {"tool_name": "read_file"})


def test_candidate_file_validates_operation_hashes_and_diff() -> None:
    modified = CandidateFile(
        path="src/app.py",
        operation=CandidateOperation.MODIFY,
        mode="100644",
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        byte_count=12,
        line_count=1,
        diff="--- a/src/app.py\n+++ b/src/app.py\n",
        content_blob_sha256="b" * 64,
    )
    added = CandidateFile(
        path="src/new.py",
        operation=CandidateOperation.ADD,
        mode="100644",
        before_sha256=None,
        after_sha256="c" * 64,
        byte_count=4,
        line_count=1,
        diff="--- /dev/null\n+++ b/src/new.py\n",
        content_blob_sha256="c" * 64,
    )

    assert modified.operation is CandidateOperation.MODIFY
    assert added.before_sha256 is None
    with pytest.raises(ValidationError):
        CandidateFile.model_validate(modified.model_dump() | {"before_sha256": None})
    with pytest.raises(ValidationError):
        CandidateFile.model_validate(added.model_dump() | {"before_sha256": "a" * 64})
    with pytest.raises(ValidationError):
        CandidateFile.model_validate(modified.model_dump() | {"diff": "x" * 65_537})


def test_candidate_manifest_hashes_canonical_ready_projection(tmp_path: Path) -> None:
    candidate_file = CandidateFile(
        path="src/app.py",
        operation=CandidateOperation.MODIFY,
        mode="100644",
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        byte_count=12,
        line_count=1,
        diff="bounded",
        content_blob_sha256="b" * 64,
    )
    manifest = CandidateManifest.create(
        candidate_id="candidate-1",
        lease_id="lease-1",
        repository_root=tmp_path.resolve(),
        base_sha="c" * 40,
        profile_id="implementation",
        child_id="child-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="d" * 64,
        disposition=CandidateDisposition.READY,
        files=(candidate_file,),
        observed_paths=("src/app.py",),
    )

    assert manifest.changed_files == 1
    with pytest.raises(ValidationError):
        CandidateManifest.model_validate(manifest.model_dump() | {"manifest_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        CandidateManifest.model_validate(
            manifest.model_dump() | {"observed_paths": ("src/other.py",)}
        )
