from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.subagents.models import SubagentProfile, SubagentStatus
from mini_code_agent.worktrees.adoption import CandidateAdoptionService
from mini_code_agent.worktrees.git import (
    WorktreeGitError,
    parse_status_paths,
)
from mini_code_agent.worktrees.manager import WorktreeManager
from mini_code_agent.worktrees.models import (
    CandidateDisposition,
    CandidateFile,
    CandidateManifest,
    CandidateOperation,
    CandidateState,
    GitIndexPointer,
    WorktreeProfile,
)
from mini_code_agent.worktrees.state import WorktreeStateError, WorktreeStateStore


def profile_for(tmp_path: Path) -> WorktreeProfile:
    repository = tmp_path / "repository"
    state = tmp_path / "state"
    executable = tmp_path / ("git.exe" if os.name == "nt" else "git")
    repository.mkdir()
    state.mkdir()
    executable.touch()
    if os.name != "nt":
        state.chmod(0o700)
        executable.chmod(0o700)
    return WorktreeProfile(
        repository_root=repository,
        state_root=state,
        git_executable=executable,
        allowed_path_prefixes=("src",),
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


def ready_candidate(
    profile: WorktreeProfile,
    *,
    candidate_id: str = "candidate-1",
) -> CandidateManifest:
    store = WorktreeStateStore(profile)
    store.initialize()
    before = b"VALUE = 1\n"
    after = b"VALUE = 2\n"
    source = profile.repository_root / "src" / "app.py"
    source.parent.mkdir()
    source.write_bytes(before)
    file = CandidateFile(
        path="src/app.py",
        operation=CandidateOperation.MODIFY,
        mode="100644",
        before_sha256=hashlib.sha256(before).hexdigest(),
        after_sha256=hashlib.sha256(after).hexdigest(),
        byte_count=len(after),
        line_count=1,
        diff="bounded",
        content_blob_sha256=hashlib.sha256(after).hexdigest(),
    )
    manifest = CandidateManifest.create(
        candidate_id=candidate_id,
        lease_id="lease-1",
        repository_root=profile.repository_root,
        base_sha="a" * 40,
        profile_id="implementation",
        child_id="child-1",
        child_status=SubagentStatus.COMPLETED,
        evidence_sha256="e" * 64,
        disposition=CandidateDisposition.READY,
        files=(file,),
        observed_paths=("src/app.py",),
    )
    store.begin_candidate(candidate_id)
    store.write_candidate_blob(candidate_id, file.content_blob_sha256, after)
    store.write_candidate_json(
        candidate_id,
        "manifest.json",
        manifest.model_dump(mode="json"),
    )
    store.transition_candidate(
        candidate_id,
        CandidateState.BUILDING,
        CandidateState.READY,
    )
    return manifest


@pytest.mark.parametrize("tamper", ["manifest", "blob", "extra"])
def test_candidate_store_fails_closed_on_state_tampering(
    tmp_path: Path,
    tamper: str,
) -> None:
    profile = profile_for(tmp_path)
    manifest = ready_candidate(profile)
    candidate = profile.state_root / "candidates" / "ready" / manifest.candidate_id
    if tamper == "manifest":
        (candidate / "manifest.json").write_text('{"forged":true}\n', encoding="utf-8")
    elif tamper == "blob":
        (candidate / "blobs" / manifest.files[0].content_blob_sha256).write_bytes(b"forged")
    else:
        (candidate / "unexpected").write_text("forged", encoding="utf-8")

    with pytest.raises(WorktreeStateError):
        WorktreeStateStore(profile).load_candidate(
            CandidateState.READY,
            manifest.candidate_id,
        )


class BlockingCreateGit:
    def __init__(self, profile: WorktreeProfile) -> None:
        self.profile = profile
        self.started = asyncio.Event()

    async def repository_info(self) -> tuple[Path, bool]:
        return self.profile.repository_root, False

    async def head_sha(self) -> str:
        return "a" * 40

    async def status_porcelain(self) -> bytes:
        return b""

    async def index_pointers(self) -> tuple[GitIndexPointer, ...]:
        return (
            GitIndexPointer(
                path="src/app.py",
                mode="100644",
                object_id="b" * 40,
            ),
        )

    async def read_blobs(self, object_ids: tuple[str, ...]) -> dict[str, bytes]:
        assert object_ids == ("b" * 40,)
        return {"b" * 40: b"VALUE = 1\n"}

    async def add_worktree(self, lease_id: str, path: Path, base_sha: str) -> None:
        assert lease_id == "lease-cancel"
        assert base_sha == "a" * 40
        path.mkdir()
        admin = path.parent / "admin"
        admin.mkdir()
        (path / ".git").write_bytes(f"gitdir: {admin}\n".encode())
        self.started.set()
        await asyncio.Event().wait()

    async def unlock_worktree(self, path: Path) -> None:
        raise AssertionError(path)

    async def lock_worktree(self, path: Path, lease_id: str) -> None:
        raise AssertionError((path, lease_id))

    async def remove_worktree(self, path: Path) -> None:
        raise AssertionError(path)

    async def prune_worktrees(self) -> None:
        raise AssertionError

    async def worktree_paths(self) -> tuple[Path, ...]:
        return ()


@pytest.mark.asyncio
async def test_cancellation_during_git_creation_retains_exact_diagnostic(
    tmp_path: Path,
) -> None:
    profile = profile_for(tmp_path)
    git = BlockingCreateGit(profile)
    manager = WorktreeManager(
        profile,
        git=git,
        id_factory=lambda: "lease-cancel",
    )
    task = asyncio.create_task(manager.create_lease(child_id="child-1"))
    await git.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    lease = profile.state_root / "leases" / "lease-cancel"
    assert (lease / "worktree").exists()
    diagnostic = json.loads((lease / "cleanup-required.json").read_text(encoding="utf-8"))
    assert diagnostic == {
        "lease_id": "lease-cancel",
        "stage": "creation_failed",
        "status": "cleanup_required",
    }


class AdoptionRaceGit:
    def __init__(self, profile: WorktreeProfile, *, head: str = "a" * 40) -> None:
        self.profile = profile
        self.head = head

    async def repository_info(self) -> tuple[Path, bool]:
        return self.profile.repository_root, False

    async def head_sha(self) -> str:
        return self.head

    async def status_porcelain(self) -> bytes:
        return b""

    async def changed_paths(self) -> tuple[str, ...]:
        return ()


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["stale_file", "head"])
async def test_adoption_races_return_ready_without_overwriting_parent(
    tmp_path: Path,
    race: str,
) -> None:
    profile = profile_for(tmp_path)
    manifest = ready_candidate(profile)
    source = profile.repository_root / "src" / "app.py"
    user_content = b"VALUE = 99\n"
    git = AdoptionRaceGit(
        profile,
        head=("f" * 40 if race == "head" else "a" * 40),
    )
    if race == "stale_file":
        source.write_bytes(user_content)
    service = CandidateAdoptionService(
        profile,
        store=WorktreeStateStore(profile),
        git=git,
    )

    result = await service.adopt(manifest.candidate_id)

    assert result.status.value == "conflict"
    assert source.read_bytes() == (user_content if race == "stale_file" else b"VALUE = 1\n")
    WorktreeStateStore(profile).load_candidate(
        CandidateState.READY,
        manifest.candidate_id,
    )


def test_hostile_status_paths_are_bounded_and_case_unique() -> None:
    payload = b" M src/line\nname.py\0?? src/tab\tname.py\0"

    assert parse_status_paths(
        payload,
        max_entries=2,
        max_path_chars=64,
    ) == ("src/line\nname.py", "src/tab\tname.py")
    with pytest.raises(WorktreeGitError):
        parse_status_paths(
            b"?? src/App.py\0?? src/app.py\0",
            max_entries=2,
            max_path_chars=64,
        )
    with pytest.raises(WorktreeGitError):
        parse_status_paths(
            payload,
            max_entries=1,
            max_path_chars=64,
        )
