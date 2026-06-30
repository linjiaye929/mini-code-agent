from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.command.models import CommandRequest, CommandResult
from mini_code_agent.git.client import GitClient
from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import GitDiffMode, GitEntryKind, GitLimits


class FakeRunner:
    def __init__(
        self,
        responses: Iterable[CommandResult | CommandError],
    ) -> None:
        self._responses = iter(responses)
        self.requests: list[CommandRequest] = []

    async def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        response = next(self._responses)
        if isinstance(response, CommandError):
            raise response
        return response


def result(
    stdout: str,
    *,
    exit_code: int = 0,
    timed_out: bool = False,
    output_limit_exceeded: bool = False,
) -> CommandResult:
    return CommandResult(
        argv=("git",),
        cwd=".",
        exit_code=exit_code,
        stdout=stdout,
        stderr="secret stderr",
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
        stdout_truncated=output_limit_exceeded,
        stderr_truncated=False,
        duration_ms=1,
    )


def git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    git(root, "add", "--", "tracked.txt")
    git(root, "commit", "-qm", "initial")
    return root


@pytest.mark.asyncio
async def test_git_client_uses_hardened_argv(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    status = "# branch.oid " + "a" * 40 + "\0# branch.head main\0? new.txt\0"
    runner = FakeRunner(
        [
            result(f"{root}\nfalse\n"),
            result(status),
            result(f"{root}\nfalse\n"),
            result("diff --git a/a b/a\n"),
        ]
    )
    client = GitClient(root, runner=runner)

    await client.status()
    await client.diff(staged=True)

    prefix = (
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.external=",
        "-C",
        str(root),
    )
    assert runner.requests[0].argv == (
        *prefix,
        "rev-parse",
        "--show-toplevel",
        "--is-bare-repository",
    )
    assert runner.requests[1].argv == (
        *prefix,
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--untracked-files=all",
        "--ignore-submodules=all",
    )
    assert runner.requests[3].argv == (
        *prefix,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=all",
        "--unified=3",
        "--cached",
        "--",
    )


@pytest.mark.asyncio
async def test_real_git_status_and_diff_do_not_mutate_index(tmp_path: Path) -> None:
    root = repository(tmp_path)
    index = root / ".git" / "index"
    before_index = index.read_bytes()
    before_mtime = index.stat().st_mtime_ns
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    (root / "-untracked.txt").write_text("new\n", encoding="utf-8")
    client = GitClient(root)

    status = await client.status()
    unstaged = await client.diff(staged=False)
    assert index.read_bytes() == before_index
    assert index.stat().st_mtime_ns == before_mtime
    git(root, "add", "--", "tracked.txt")
    staged = await client.diff(staged=True)

    assert {entry.kind for entry in status.entries} == {
        GitEntryKind.ORDINARY,
        GitEntryKind.UNTRACKED,
    }
    assert "after" in unstaged.patch
    assert unstaged.mode is GitDiffMode.UNSTAGED
    assert "after" in staged.patch
    assert staged.mode is GitDiffMode.STAGED


@pytest.mark.asyncio
async def test_status_call_itself_leaves_index_byte_identical(tmp_path: Path) -> None:
    root = repository(tmp_path)
    index = root / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    await GitClient(root).status()

    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


@pytest.mark.asyncio
async def test_hardening_disables_configured_execution_extensions(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    marker = root / "extension-ran.txt"
    if os.name == "nt":
        extension = root / "extension.bat"
        extension.write_text(
            f'@echo off\r\necho called>"{marker}"\r\nexit /b 0\r\n',
            encoding="utf-8",
        )
    else:
        extension = root / "extension.sh"
        extension.write_text(
            f"#!/bin/sh\nprintf called > '{marker}'\n",
            encoding="utf-8",
        )
        extension.chmod(0o700)
    git(root, "config", "core.fsmonitor", str(extension))
    git(root, "config", "diff.external", str(extension))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    client = GitClient(root)
    await client.status()
    await client.diff()

    assert not marker.exists()


@pytest.mark.asyncio
async def test_git_client_rejects_nested_workspace_and_non_repository(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    nested = root / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(GitError) as nested_error:
        await GitClient(nested).status()
    with pytest.raises(GitError) as outside_error:
        await GitClient(outside).status()

    assert nested_error.value.code is GitErrorCode.OUTSIDE_WORKSPACE
    assert outside_error.value.code is GitErrorCode.NOT_REPOSITORY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (result("", timed_out=True), GitErrorCode.TIMEOUT),
        (result("", output_limit_exceeded=True), GitErrorCode.LIMIT_EXCEEDED),
        (result("", exit_code=1), GitErrorCode.NOT_REPOSITORY),
        (
            CommandError(
                CommandErrorCode.COMMAND_NOT_FOUND,
                "secret executable error",
            ),
            GitErrorCode.UNAVAILABLE,
        ),
    ],
)
async def test_git_client_normalizes_runner_failures(
    tmp_path: Path,
    response: CommandResult | CommandError,
    code: GitErrorCode,
) -> None:
    runner = FakeRunner([response])

    with pytest.raises(GitError) as captured:
        await GitClient(tmp_path, runner=runner).status()

    assert captured.value.code is code
    assert "secret" not in captured.value.public_message


@pytest.mark.asyncio
async def test_git_diff_rejects_patch_character_limit(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    runner = FakeRunner(
        [
            result(f"{root}\nfalse\n"),
            result("x" * 1_025),
        ]
    )
    client = GitClient(
        root,
        runner=runner,
        limits=GitLimits(max_patch_chars=1_024),
    )

    with pytest.raises(GitError) as captured:
        await client.diff()

    assert captured.value.code is GitErrorCode.LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_tracked_paths_uses_exact_hardened_pathspecs(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    runner = FakeRunner(
        [
            result(f"{root}\nfalse\n"),
            result("src/a.py\0tests/test_a.py\0"),
        ]
    )
    client = GitClient(root, runner=runner)

    tracked = await client.tracked_paths(("src/a.py", "tests/test_a.py"))

    assert client.workspace_root == root
    assert tracked == ("src/a.py", "tests/test_a.py")
    assert runner.requests[-1].argv[-6:] == (
        "ls-files",
        "--error-unmatch",
        "-z",
        "--",
        ":(top,literal)src/a.py",
        ":(top,literal)tests/test_a.py",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stdout",
    (
        "src/a.py\0",
        "src/a.py\0tests/test_a.py\0extra.py\0",
        "src/a.py\0src/a.py\0tests/test_a.py\0",
        "src/a.py\0tests/test_a.py",
        "src/a.py\0bad\ufffd.py\0",
    ),
)
async def test_tracked_paths_rejects_non_exact_or_malformed_output(
    tmp_path: Path,
    stdout: str,
) -> None:
    root = tmp_path.resolve()
    runner = FakeRunner(
        [
            result(f"{root}\nfalse\n"),
            result(stdout),
        ]
    )

    with pytest.raises(GitError) as captured:
        await GitClient(root, runner=runner).tracked_paths(("src/a.py", "tests/test_a.py"))

    assert captured.value.code is GitErrorCode.INVALID_OUTPUT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paths",
    (
        (),
        ("src/a.py", "src/a.py"),
        ("bad\0path.py",),
        ("x" * 4_097,),
        tuple(f"src/{index}.py" for index in range(33)),
    ),
)
async def test_tracked_paths_rejects_invalid_requests(
    tmp_path: Path,
    paths: tuple[str, ...],
) -> None:
    client = GitClient(tmp_path, runner=FakeRunner([]))

    with pytest.raises(GitError) as captured:
        await client.tracked_paths(paths)

    assert captured.value.code is GitErrorCode.INVALID_OUTPUT


@pytest.mark.asyncio
async def test_real_tracked_paths_rejects_ignored_file(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    client = GitClient(root)

    assert await client.tracked_paths(("tracked.txt",)) == ("tracked.txt",)
    with pytest.raises(GitError) as captured:
        await client.tracked_paths(("ignored.txt",))

    assert captured.value.code is GitErrorCode.COMMAND_FAILED
