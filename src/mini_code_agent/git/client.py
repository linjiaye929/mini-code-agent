from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from mini_code_agent.command.errors import CommandError
from mini_code_agent.command.models import (
    CommandLimits,
    CommandRequest,
    CommandResult,
)
from mini_code_agent.command.runner import CommandRunner
from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import (
    GitDiffMode,
    GitDiffResult,
    GitLimits,
    GitStatusSnapshot,
)
from mini_code_agent.git.porcelain import parse_porcelain_v2


class GitCommandRunner(Protocol):
    async def run(self, request: CommandRequest) -> CommandResult: ...


class GitClient:
    def __init__(
        self,
        workspace_root: Path,
        *,
        runner: GitCommandRunner | None = None,
        limits: GitLimits | None = None,
        executable: str = "git",
    ) -> None:
        try:
            root = workspace_root.resolve(strict=True)
        except OSError:
            raise ValueError("Git workspace must be an existing directory.") from None
        if not root.is_dir():
            raise ValueError("Git workspace must be an existing directory.")
        self._root = root
        self._limits = limits or GitLimits()
        self._runner = runner or CommandRunner(
            limits=CommandLimits(
                max_output_bytes=self._limits.max_output_bytes,
                max_timeout_seconds=self._limits.command_timeout_seconds,
            )
        )
        self._prefix = (
            executable,
            "--no-pager",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "diff.external=",
            "-C",
            str(root),
        )

    async def status(self) -> GitStatusSnapshot:
        await self._verify_repository()
        output = await self._execute(
            (
                *self._prefix,
                "status",
                "--porcelain=v2",
                "-z",
                "--branch",
                "--untracked-files=all",
                "--ignore-submodules=all",
            ),
            failure_code=GitErrorCode.COMMAND_FAILED,
        )
        return parse_porcelain_v2(
            output,
            max_entries=self._limits.max_status_entries,
        )

    async def diff(self, *, staged: bool = False) -> GitDiffResult:
        await self._verify_repository()
        mode = GitDiffMode.STAGED if staged else GitDiffMode.UNSTAGED
        cached = ("--cached",) if staged else ()
        patch = await self._execute(
            (
                *self._prefix,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=all",
                "--unified=3",
                *cached,
                "--",
            ),
            failure_code=GitErrorCode.COMMAND_FAILED,
        )
        if len(patch) > self._limits.max_patch_chars:
            raise GitError(GitErrorCode.LIMIT_EXCEEDED)
        encoded = patch.encode("utf-8")
        return GitDiffResult(
            mode=mode,
            patch=patch,
            byte_count=len(encoded),
            char_count=len(patch),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )

    async def _verify_repository(self) -> None:
        output = await self._execute(
            (
                *self._prefix,
                "rev-parse",
                "--show-toplevel",
                "--is-bare-repository",
            ),
            failure_code=GitErrorCode.NOT_REPOSITORY,
        )
        lines = output.splitlines()
        if len(lines) != 2:
            raise GitError(GitErrorCode.INVALID_OUTPUT)
        if lines[1] == "true":
            raise GitError(GitErrorCode.BARE_REPOSITORY)
        if lines[1] != "false":
            raise GitError(GitErrorCode.INVALID_OUTPUT)
        try:
            top_level = Path(lines[0]).resolve(strict=True)
        except OSError:
            raise GitError(GitErrorCode.INVALID_OUTPUT) from None
        if top_level != self._root:
            raise GitError(GitErrorCode.OUTSIDE_WORKSPACE)

    async def _execute(
        self,
        argv: tuple[str, ...],
        *,
        failure_code: GitErrorCode,
    ) -> str:
        try:
            result = await self._runner.run(
                CommandRequest(
                    argv=argv,
                    cwd=self._root,
                    cwd_display=".",
                    timeout_seconds=self._limits.command_timeout_seconds,
                )
            )
        except CommandError:
            raise GitError(GitErrorCode.UNAVAILABLE) from None
        if result.timed_out:
            raise GitError(GitErrorCode.TIMEOUT)
        if result.output_limit_exceeded:
            raise GitError(GitErrorCode.LIMIT_EXCEEDED)
        if result.exit_code != 0:
            raise GitError(failure_code)
        if "\ufffd" in result.stdout:
            raise GitError(GitErrorCode.INVALID_OUTPUT)
        return result.stdout
