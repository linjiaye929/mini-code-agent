from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mini_code_agent.worktrees.git import (
    GitByteCommand,
    GitByteResult,
    GitBytesRunner,
    WorktreeGit,
    WorktreeGitError,
    parse_batch_blobs,
    parse_index_pointers,
    parse_status_paths,
    parse_worktree_paths,
)
from mini_code_agent.worktrees.models import WorktreeErrorCode
from mini_code_agent.worktrees.state import WorktreeStateStore

from .helpers import worktree_profile


class RecordingRunner:
    def __init__(self, *results: GitByteResult) -> None:
        self.results = list(results)
        self.commands: list[GitByteCommand] = []

    async def run(self, command: GitByteCommand) -> GitByteResult:
        self.commands.append(command)
        return self.results.pop(0)


def result(stdout: bytes = b"", *, exit_code: int = 0) -> GitByteResult:
    return GitByteResult(
        stdout=stdout,
        stderr=b"",
        exit_code=exit_code,
        timed_out=False,
        output_limit_exceeded=False,
    )


@pytest.mark.asyncio
async def test_git_uses_pinned_executable_fixed_prefix_and_no_shell(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    runner = RecordingRunner(result(b"a" * 40 + b"\n"))
    git = WorktreeGit(profile, runner=runner)

    assert await git.head_sha() == "a" * 40
    command = runner.commands[0]
    assert command.argv == (
        str(profile.git_executable),
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={profile.state_root / 'hooks-empty'}",
        "-C",
        str(profile.repository_root),
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    )
    assert command.cwd == profile.repository_root
    assert command.stdin is None


@pytest.mark.asyncio
async def test_git_revalidates_executable_before_every_command(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    runner = RecordingRunner(result(b"a" * 40 + b"\n"))
    git = WorktreeGit(profile, runner=runner)
    profile.git_executable.unlink()

    with pytest.raises(WorktreeGitError) as raised:
        await git.head_sha()

    assert raised.value.code is WorktreeErrorCode.REPOSITORY_UNSUPPORTED
    assert runner.commands == []


@pytest.mark.asyncio
async def test_git_worktree_mutations_require_exact_host_lease_path(
    tmp_path: Path,
) -> None:
    profile = worktree_profile(tmp_path)
    store = WorktreeStateStore(profile)
    store.initialize()
    paths = store.begin_lease("lease-1")
    runner = RecordingRunner(result())
    git = WorktreeGit(profile, runner=runner)

    await git.add_worktree("lease-1", paths.worktree, "a" * 40)

    assert runner.commands[0].argv[-9:] == (
        "worktree",
        "add",
        "--detach",
        "--no-checkout",
        "--lock",
        "--reason",
        "mini-code-agent:lease-1",
        str(paths.worktree),
        "a" * 40,
    )
    with pytest.raises(WorktreeGitError):
        await git.add_worktree("other", paths.worktree, "a" * 40)
    with pytest.raises(WorktreeGitError):
        await git.add_worktree(
            "lease-1",
            tmp_path / "outside" / "worktree",
            "a" * 40,
        )
    with pytest.raises(WorktreeGitError):
        await git.add_worktree("lease-1", paths.worktree, "invalid")
    assert len(runner.commands) == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"100644 " + b"a" * 40 + b" 0\tsrc/app.py",
        b"100644 " + b"a" * 40 + b" 1\tsrc/app.py\0",
        b"120000 " + b"a" * 40 + b" 0\tsrc/app.py\0",
        b"100644 "
        + b"a" * 40
        + b" 0\tsrc/app.py\x00"
        + b"100644 "
        + b"b" * 40
        + b" 0\tSRC/app.py\x00",
        b"100644 " + b"a" * 40 + b" 0\tsrc/\xff.py\0",
    ],
)
def test_index_parser_rejects_truncated_unsupported_or_colliding_entries(
    payload: bytes,
) -> None:
    with pytest.raises(WorktreeGitError):
        parse_index_pointers(payload, max_entries=20_000, max_path_chars=1024)


def test_index_parser_accepts_hostile_but_valid_nul_delimited_names() -> None:
    payload = (
        b"100644 " + b"a" * 40 + b" 0\tsrc/name with newline\nand tab\t.py\0"
        b"100755 " + b"b" * 40 + b" 0\ttests/run.py\0"
    )

    entries = parse_index_pointers(payload, max_entries=20_000, max_path_chars=1024)

    assert [entry.path for entry in entries] == [
        "src/name with newline\nand tab\t.py",
        "tests/run.py",
    ]
    assert entries[1].mode == "100755"


def test_batch_blob_parser_validates_order_size_and_terminators() -> None:
    first = b"a" * 40
    second = b"b" * 40
    payload = first + b" blob 3\none\n" + second + b" blob 3\ntwo\n"

    blobs = parse_batch_blobs(payload, (first.decode(), second.decode()), max_total_bytes=6)

    assert blobs == {first.decode(): b"one", second.decode(): b"two"}
    with pytest.raises(WorktreeGitError):
        parse_batch_blobs(payload[:-1], (first.decode(), second.decode()), max_total_bytes=6)
    with pytest.raises(WorktreeGitError):
        parse_batch_blobs(payload, (second.decode(), first.decode()), max_total_bytes=6)
    with pytest.raises(WorktreeGitError):
        parse_batch_blobs(payload, (first.decode(), second.decode()), max_total_bytes=5)


def test_worktree_list_parser_extracts_absolute_unique_paths(tmp_path: Path) -> None:
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    payload = (
        f"worktree {first}\0HEAD {'a' * 40}\0locked reason\0\0"
        f"worktree {second}\0HEAD {'b' * 40}\0detached\0\0"
    ).encode()

    assert parse_worktree_paths(payload) == (first, second)
    with pytest.raises(WorktreeGitError):
        parse_worktree_paths(payload.rstrip(b"\0"))
    with pytest.raises(WorktreeGitError):
        parse_worktree_paths(f"worktree {first}\0worktree {first}\0".encode())


def test_status_path_parser_accepts_modifications_and_additions() -> None:
    payload = b" M src/app.py\0?? src/new.py\0"

    assert parse_status_paths(
        payload,
        max_entries=32,
        max_path_chars=1024,
    ) == ("src/app.py", "src/new.py")
    with pytest.raises(WorktreeGitError):
        parse_status_paths(
            payload.rstrip(b"\0"),
            max_entries=32,
            max_path_chars=1024,
        )
    with pytest.raises(WorktreeGitError):
        parse_status_paths(
            b"R  src/renamed.py\0src/original.py\0",
            max_entries=32,
            max_path_chars=1024,
        )


@pytest.mark.asyncio
async def test_byte_runner_enforces_output_and_timeout_limits(tmp_path: Path) -> None:
    runner = GitBytesRunner(cleanup_timeout_seconds=2)
    output_command = GitByteCommand(
        argv=(sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 10000)"),
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_bytes=128,
    )
    timeout_command = GitByteCommand(
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        timeout_seconds=0.1,
        max_output_bytes=128,
    )

    output = await runner.run(output_command)
    timed_out = await runner.run(timeout_command)

    assert output.output_limit_exceeded is True
    assert len(output.stdout) + len(output.stderr) <= 128
    assert timed_out.timed_out is True


@pytest.mark.asyncio
async def test_git_rejects_failed_timed_out_or_truncated_commands(tmp_path: Path) -> None:
    profile = worktree_profile(tmp_path)
    failures = [
        GitByteResult(
            stdout=b"",
            stderr=b"secret",
            exit_code=1,
            timed_out=False,
            output_limit_exceeded=False,
        ),
        GitByteResult(
            stdout=b"",
            stderr=b"",
            exit_code=None,
            timed_out=True,
            output_limit_exceeded=False,
        ),
        GitByteResult(
            stdout=b"a" * 40,
            stderr=b"",
            exit_code=0,
            timed_out=False,
            output_limit_exceeded=True,
        ),
    ]

    for failure in failures:
        git = WorktreeGit(profile, runner=RecordingRunner(failure))
        with pytest.raises(WorktreeGitError):
            await git.head_sha()


@pytest.mark.asyncio
async def test_git_reads_real_repository_index_and_raw_blobs(tmp_path: Path) -> None:
    discovered_git = shutil.which("git")
    if discovered_git is None:
        pytest.skip("Git is unavailable.")
    git_executable = Path(discovered_git).resolve(strict=True)
    profile = worktree_profile(tmp_path, git_executable=git_executable)
    WorktreeStateStore(profile).initialize()
    content = b"\x00raw\r\nbytes\xff"
    tracked = profile.repository_root / "src" / "raw.bin"
    tracked.parent.mkdir()
    tracked.write_bytes(content)
    _git(profile.repository_root, "init")
    _git(profile.repository_root, "config", "user.email", "agent@example.invalid")
    _git(profile.repository_root, "config", "user.name", "Agent Test")
    _git(profile.repository_root, "add", "--", "src/raw.bin")
    _git(profile.repository_root, "commit", "-m", "initial")
    git = WorktreeGit(profile)

    top_level, bare = await git.repository_info()
    head = await git.head_sha()
    status = await git.status_porcelain()
    pointers = await git.index_pointers()
    blobs = await git.read_blobs(tuple(pointer.object_id for pointer in pointers))

    assert top_level == profile.repository_root
    assert bare is False
    assert len(head) == 40
    assert status == b""
    assert len(pointers) == 1
    assert pointers[0].path == "src/raw.bin"
    assert blobs[pointers[0].object_id] == content


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )
