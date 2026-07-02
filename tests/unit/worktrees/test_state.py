from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mini_code_agent.worktrees.models import CandidateState
from mini_code_agent.worktrees.state import WorktreeStateError, WorktreeStateStore

from .helpers import worktree_profile


def test_state_store_initializes_private_fixed_layout(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    store = WorktreeStateStore(profile)

    store.initialize()

    expected = {
        "leases",
        "hooks-empty",
        *(f"candidates/{state.value}" for state in CandidateState),
    }
    assert expected <= {
        path.relative_to(profile.state_root).as_posix()
        for path in profile.state_root.rglob("*")
        if path.is_dir()
    }


@pytest.mark.parametrize("candidate_id", ["../escape", "a/b", ".hidden", "x" * 97, "a\0b"])
def test_state_store_rejects_non_opaque_identifiers(
    tmp_path: Path,
    candidate_id: str,
) -> None:
    store = WorktreeStateStore(worktree_profile(tmp_path))
    store.initialize()

    with pytest.raises(WorktreeStateError):
        store.begin_candidate(candidate_id)


def test_state_store_writes_canonical_json_and_content_addressed_blob(
    tmp_path: Path,
) -> None:
    profile = worktree_profile(tmp_path)
    store = WorktreeStateStore(profile)
    store.initialize()
    candidate = store.begin_candidate("candidate-1")
    content = b"print('safe')\n"
    digest = hashlib.sha256(content).hexdigest()

    manifest_path = store.write_candidate_json(
        "candidate-1",
        "manifest.json",
        {"z": 1, "a": "value"},
    )
    blob_path = store.write_candidate_blob("candidate-1", digest, content)

    assert candidate == profile.state_root / "candidates" / "building" / "candidate-1"
    assert manifest_path.read_bytes() == b'{"a":"value","z":1}\n'
    assert blob_path.read_bytes() == content
    assert not list(candidate.rglob("*.tmp"))
    with pytest.raises(WorktreeStateError):
        store.write_candidate_blob("candidate-1", "0" * 64, content)


def test_state_store_transitions_by_atomic_directory_rename(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    store = WorktreeStateStore(profile)
    store.initialize()
    store.begin_candidate("candidate-1")
    store.write_candidate_json("candidate-1", "manifest.json", {"ready": True})

    ready = store.transition_candidate(
        "candidate-1",
        CandidateState.BUILDING,
        CandidateState.READY,
    )

    assert ready == profile.state_root / "candidates" / "ready" / "candidate-1"
    assert json.loads((ready / "manifest.json").read_text(encoding="utf-8")) == {"ready": True}
    with pytest.raises(WorktreeStateError):
        store.transition_candidate(
            "candidate-1",
            CandidateState.BUILDING,
            CandidateState.READY,
        )


def test_state_store_refuses_linked_managed_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = worktree_profile(tmp_path)
    store = WorktreeStateStore(profile)

    def marks_leases_as_link(path: Path) -> bool:
        return path.name == "leases"

    monkeypatch.setattr(
        "mini_code_agent.worktrees.state._is_link_or_reparse",
        marks_leases_as_link,
    )

    with pytest.raises(WorktreeStateError):
        store.initialize()
