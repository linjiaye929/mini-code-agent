from __future__ import annotations

import asyncio
import os
import re
import signal
import stat
import subprocess  # nosec B404
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import ValidationError

from mini_code_agent.command.environment import build_minimal_environment
from mini_code_agent.worktrees.models import (
    GitIndexPointer,
    WorktreeError,
    WorktreeErrorCode,
    WorktreeProfile,
)

_INDEX_HEADER = re.compile(rb"^(100644|100755) ([0-9a-f]{40}) ([0-3])\t")
_BATCH_HEADER = re.compile(rb"^([0-9a-f]{40}) blob ([0-9]+)$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_READ_CHUNK_BYTES = 64 * 1024


class WorktreeGitError(WorktreeError):
    pass


@dataclass(frozen=True, slots=True)
class GitByteCommand:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    max_output_bytes: int
    stdin: bytes | None = None

    def __post_init__(self) -> None:
        if (
            not self.argv
            or any(not argument or "\0" in argument for argument in self.argv)
            or not self.cwd.is_absolute()
            or not self.cwd.is_dir()
            or not 0 < self.timeout_seconds <= 300
            or not 1 <= self.max_output_bytes <= 1024 * 1024 * 1024
            or (self.stdin is not None and len(self.stdin) > 2 * 1024 * 1024)
        ):
            raise ValueError("Invalid Git byte command.")


@dataclass(frozen=True, slots=True)
class GitByteResult:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    timed_out: bool
    output_limit_exceeded: bool


class GitByteCommandRunner(Protocol):
    async def run(self, command: GitByteCommand) -> GitByteResult: ...


@dataclass(slots=True)
class _OutputBudget:
    limit: int
    used: int = 0
    exceeded: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def retain(self, chunk: bytes) -> bytes:
        async with self.lock:
            remaining = self.limit - self.used
            kept = chunk[:remaining]
            self.used += len(kept)
            if len(kept) != len(chunk):
                self.exceeded.set()
            return kept


class GitBytesRunner:
    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        cleanup_timeout_seconds: float = 5,
    ) -> None:
        if not 0 < cleanup_timeout_seconds <= 30:
            raise ValueError("Git cleanup timeout is invalid.")
        self._environment = build_minimal_environment(
            os.environ if environment is None else environment
        )
        self._cleanup_timeout_seconds = cleanup_timeout_seconds

    async def run(self, command: GitByteCommand) -> GitByteResult:
        process = await self._start(command)
        stdout = bytearray()
        stderr = bytearray()
        budget = _OutputBudget(command.max_output_bytes)
        readers = (
            asyncio.create_task(self._read(process.stdout, stdout, budget)),
            asyncio.create_task(self._read(process.stderr, stderr, budget)),
        )
        writer = asyncio.create_task(self._write_stdin(process, command.stdin))
        process_wait = asyncio.create_task(process.wait())
        output_wait = asyncio.create_task(budget.exceeded.wait())
        timed_out = False
        try:
            done, _ = await asyncio.wait(
                (process_wait, output_wait),
                timeout=command.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                await self._terminate_tree(process)
            elif output_wait in done and output_wait.result():
                exited, _ = await asyncio.wait((process_wait,), timeout=0.1)
                if not exited:
                    await self._terminate_tree(process)
            else:
                process_wait.result()
            await asyncio.gather(writer, *readers)
        except asyncio.CancelledError:
            await asyncio.shield(self._terminate_tree(process))
            await self._cancel_tasks((writer, *readers))
            raise
        except Exception:
            await self._best_effort_terminate(process)
            await self._cancel_tasks((writer, *readers))
            raise WorktreeGitError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Git command I/O failed.",
            ) from None
        finally:
            output_wait.cancel()
            await asyncio.gather(output_wait, return_exceptions=True)
            if not process_wait.done():
                process_wait.cancel()
                await asyncio.gather(process_wait, return_exceptions=True)
        return GitByteResult(
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            exit_code=process.returncode,
            timed_out=timed_out,
            output_limit_exceeded=budget.exceeded.is_set(),
        )

    async def _start(self, command: GitByteCommand) -> asyncio.subprocess.Process:
        creation_flags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )
        try:
            return await asyncio.create_subprocess_exec(
                *command.argv,
                cwd=command.cwd,
                env=self._environment,
                stdin=(
                    asyncio.subprocess.PIPE
                    if command.stdin is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=start_new_session,
                creationflags=creation_flags,
            )
        except (FileNotFoundError, OSError):
            raise WorktreeGitError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Git executable could not be started.",
            ) from None

    @staticmethod
    async def _read(
        stream: asyncio.StreamReader | None,
        destination: bytearray,
        budget: _OutputBudget,
    ) -> None:
        if stream is None:
            return
        while chunk := await stream.read(_READ_CHUNK_BYTES):
            destination.extend(await budget.retain(chunk))

    @staticmethod
    async def _write_stdin(
        process: asyncio.subprocess.Process,
        content: bytes | None,
    ) -> None:
        if content is None or process.stdin is None:
            return
        try:
            process.stdin.write(content)
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            return

    async def _best_effort_terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            await asyncio.gather(self._terminate_tree(process), return_exceptions=True)

    async def _terminate_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                await self._terminate_windows_tree(process)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, ChildProcessError):
            return
        except OSError:
            process.kill()
        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                await process.wait()
        except TimeoutError:
            process.kill()
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                await process.wait()

    async def _terminate_windows_tree(self, process: asyncio.subprocess.Process) -> None:
        system_root = self._environment.get("SYSTEMROOT")
        if system_root is None:
            raise OSError("Windows system root is unavailable.")
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        killer = await asyncio.create_subprocess_exec(
            str(taskkill),
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            env=self._environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        async with asyncio.timeout(self._cleanup_timeout_seconds):
            await killer.wait()

    @staticmethod
    async def _cancel_tasks(tasks: tuple[asyncio.Task[None], ...]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class WorktreeGit:
    def __init__(
        self,
        profile: WorktreeProfile,
        *,
        runner: GitByteCommandRunner | None = None,
        command_timeout_seconds: float = 30,
    ) -> None:
        if not 0 < command_timeout_seconds <= 300:
            raise ValueError("Git command timeout is invalid.")
        self._profile = profile
        self._runner = runner or GitBytesRunner(
            cleanup_timeout_seconds=min(30, profile.limits.cleanup_timeout_seconds)
        )
        self._timeout = command_timeout_seconds

    async def repository_info(self) -> tuple[Path, bool]:
        output = await self._execute(
            ("rev-parse", "--show-toplevel", "--is-bare-repository"),
            max_output_bytes=16 * 1024,
        )
        lines = _decode_lines(output)
        if len(lines) != 2 or lines[1] not in {"true", "false"}:
            raise _invalid_git_output()
        try:
            top_level = Path(lines[0]).resolve(strict=True)
        except OSError:
            raise _invalid_git_output() from None
        return top_level, lines[1] == "true"

    async def head_sha(self) -> str:
        output = await self._execute(
            ("rev-parse", "--verify", "HEAD^{commit}"),
            max_output_bytes=1024,
        )
        value = _decode_single_line(output)
        if _SHA1.fullmatch(value) is None:
            raise _invalid_git_output()
        return value

    async def status_porcelain(self) -> bytes:
        return await self._execute(
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ),
            max_output_bytes=16 * 1024 * 1024,
        )

    async def changed_paths(self) -> tuple[str, ...]:
        output = await self._execute(
            (
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ),
            max_output_bytes=16 * 1024 * 1024,
        )
        return parse_status_paths(
            output,
            max_entries=self._profile.limits.max_candidate_files,
            max_path_chars=self._profile.limits.max_path_chars,
        )

    async def index_pointers(self) -> tuple[GitIndexPointer, ...]:
        output = await self._execute(
            ("ls-files", "--stage", "--sparse", "-z"),
            max_output_bytes=32 * 1024 * 1024,
        )
        return parse_index_pointers(
            output,
            max_entries=self._profile.limits.max_tracked_files,
            max_path_chars=self._profile.limits.max_path_chars,
        )

    async def read_blobs(self, object_ids: tuple[str, ...]) -> dict[str, bytes]:
        if (
            not object_ids
            or len(object_ids) > self._profile.limits.max_tracked_files
            or len(set(object_ids)) != len(object_ids)
            or any(_SHA1.fullmatch(object_id) is None for object_id in object_ids)
        ):
            raise _invalid_git_output()
        request = "".join(f"{object_id}\n" for object_id in object_ids).encode("ascii")
        overhead = len(object_ids) * 80
        output = await self._execute(
            ("cat-file", "--batch"),
            stdin=request,
            max_output_bytes=self._profile.limits.max_tracked_bytes + overhead,
        )
        return parse_batch_blobs(
            output,
            object_ids,
            max_total_bytes=self._profile.limits.max_tracked_bytes,
        )

    async def add_worktree(self, lease_id: str, path: Path, base_sha: str) -> None:
        self._validate_lease_worktree_path(path, lease_id=lease_id, require_exists=False)
        if _SHA1.fullmatch(base_sha) is None:
            raise _invalid_git_output()
        await self._execute(
            (
                "worktree",
                "add",
                "--detach",
                "--no-checkout",
                "--lock",
                "--reason",
                f"mini-code-agent:{lease_id}",
                str(path),
                base_sha,
            ),
            max_output_bytes=1024 * 1024,
        )

    async def unlock_worktree(self, path: Path) -> None:
        self._validate_lease_worktree_path(path, require_exists=True)
        await self._execute(
            ("worktree", "unlock", str(path)),
            max_output_bytes=1024 * 1024,
        )

    async def lock_worktree(self, path: Path, lease_id: str) -> None:
        self._validate_lease_worktree_path(
            path,
            lease_id=lease_id,
            require_exists=True,
        )
        await self._execute(
            (
                "worktree",
                "lock",
                "--reason",
                f"mini-code-agent:{lease_id}",
                str(path),
            ),
            max_output_bytes=1024 * 1024,
        )

    async def remove_worktree(self, path: Path) -> None:
        self._validate_lease_worktree_path(path, require_exists=True)
        await self._execute(
            ("worktree", "remove", "--force", str(path)),
            max_output_bytes=1024 * 1024,
        )

    async def prune_worktrees(self) -> None:
        await self._execute(
            ("worktree", "prune", "--expire", "now"),
            max_output_bytes=1024 * 1024,
        )

    async def worktree_list(self) -> bytes:
        return await self._execute(
            ("worktree", "list", "--porcelain", "-z"),
            max_output_bytes=16 * 1024 * 1024,
        )

    async def worktree_paths(self) -> tuple[Path, ...]:
        return parse_worktree_paths(await self.worktree_list())

    def _validate_lease_worktree_path(
        self,
        path: Path,
        *,
        lease_id: str | None = None,
        require_exists: bool,
    ) -> None:
        if not path.is_absolute() or path.name != "worktree":
            raise _invalid_git_output()
        container = path.parent
        expected_lease_root = self._profile.state_root / "leases"
        if (
            _IDENTIFIER.fullmatch(container.name) is None
            or (lease_id is not None and container.name != lease_id)
            or not container.is_dir()
            or _is_link_or_reparse(container)
            or _is_link_or_reparse(expected_lease_root)
        ):
            raise _invalid_git_output()
        try:
            if container.parent.resolve(strict=True) != expected_lease_root.resolve(strict=True):
                raise _invalid_git_output()
            if require_exists and path.resolve(strict=True) != path:
                raise _invalid_git_output()
        except OSError:
            raise _invalid_git_output() from None
        if require_exists and (_is_link_or_reparse(path) or not path.is_dir()):
            raise _invalid_git_output()

    async def _execute(
        self,
        operation: tuple[str, ...],
        *,
        max_output_bytes: int,
        stdin: bytes | None = None,
    ) -> bytes:
        if not _is_regular_unlinked_file(self._profile.git_executable):
            raise WorktreeGitError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Pinned Git executable is unavailable.",
            )
        prefix = (
            str(self._profile.git_executable),
            "--no-pager",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={self._profile.state_root / 'hooks-empty'}",
            "-C",
            str(self._profile.repository_root),
        )
        result = await self._runner.run(
            GitByteCommand(
                argv=(*prefix, *operation),
                cwd=self._profile.repository_root,
                timeout_seconds=self._timeout,
                max_output_bytes=max_output_bytes,
                stdin=stdin,
            )
        )
        if result.timed_out:
            raise WorktreeGitError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Git command timed out.",
            )
        if result.output_limit_exceeded:
            raise WorktreeGitError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Git command output exceeded its limit.",
            )
        if result.exit_code != 0:
            raise WorktreeGitError(
                WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
                "Git command failed.",
            )
        return result.stdout


def parse_index_pointers(
    output: bytes,
    *,
    max_entries: int,
    max_path_chars: int,
) -> tuple[GitIndexPointer, ...]:
    if output and not output.endswith(b"\0"):
        raise _invalid_git_output()
    raw_records = output[:-1].split(b"\0") if output else []
    if len(raw_records) > max_entries or any(not record for record in raw_records):
        raise _invalid_git_output()
    entries: list[GitIndexPointer] = []
    casefolded: set[str] = set()
    for record in raw_records:
        matched = _INDEX_HEADER.match(record)
        if matched is None:
            raise _invalid_git_output()
        mode, object_id, stage = matched.groups()
        if stage != b"0":
            raise _invalid_git_output()
        try:
            path = record[matched.end() :].decode("utf-8")
            parsed_mode = mode.decode("ascii")
            if parsed_mode not in {"100644", "100755"}:
                raise _invalid_git_output()
            entry = GitIndexPointer(
                path=path,
                mode=cast(Literal["100644", "100755"], parsed_mode),
                object_id=object_id.decode("ascii"),
                stage=0,
            )
        except (UnicodeDecodeError, ValidationError):
            raise _invalid_git_output() from None
        folded = entry.path.casefold()
        if folded in casefolded or len(entry.path) > max_path_chars:
            raise _invalid_git_output()
        casefolded.add(folded)
        entries.append(entry)
    return tuple(entries)


def parse_batch_blobs(
    output: bytes,
    object_ids: tuple[str, ...],
    *,
    max_total_bytes: int,
) -> dict[str, bytes]:
    position = 0
    total = 0
    blobs: dict[str, bytes] = {}
    for expected in object_ids:
        newline = output.find(b"\n", position)
        if newline < 0:
            raise _invalid_git_output()
        matched = _BATCH_HEADER.fullmatch(output[position:newline])
        if matched is None or matched.group(1).decode("ascii") != expected:
            raise _invalid_git_output()
        size = int(matched.group(2))
        total += size
        if total > max_total_bytes:
            raise _invalid_git_output()
        start = newline + 1
        end = start + size
        if end >= len(output) or output[end : end + 1] != b"\n":
            raise _invalid_git_output()
        blobs[expected] = output[start:end]
        position = end + 1
    if position != len(output) or len(blobs) != len(object_ids):
        raise _invalid_git_output()
    return blobs


def parse_worktree_paths(output: bytes) -> tuple[Path, ...]:
    if output and not output.endswith(b"\0"):
        raise _invalid_git_output()
    paths: list[Path] = []
    identities: set[str] = set()
    for record in output[:-1].split(b"\0") if output else ():
        if not record:
            continue
        if not record.startswith(b"worktree "):
            continue
        try:
            raw_path = record[len(b"worktree ") :].decode("utf-8")
            path = Path(raw_path)
        except UnicodeDecodeError:
            raise _invalid_git_output() from None
        if not path.is_absolute():
            raise _invalid_git_output()
        identity = os.path.normcase(str(path))
        if identity in identities:
            raise _invalid_git_output()
        identities.add(identity)
        paths.append(path)
    return tuple(paths)


def parse_status_paths(
    output: bytes,
    *,
    max_entries: int,
    max_path_chars: int,
) -> tuple[str, ...]:
    if output and not output.endswith(b"\0"):
        raise _invalid_git_output()
    paths: list[str] = []
    identities: set[str] = set()
    for record in output[:-1].split(b"\0") if output else ():
        if (
            len(record) < 4
            or record[2:3] != b" "
            or record[:1] in {b"R", b"C"}
            or record[1:2] in {b"R", b"C"}
        ):
            raise _invalid_git_output()
        try:
            path = record[3:].decode("utf-8")
        except UnicodeDecodeError:
            raise _invalid_git_output() from None
        if not path or "\0" in path or len(path) > max_path_chars:
            raise _invalid_git_output()
        identity = path.casefold()
        if identity in identities:
            raise _invalid_git_output()
        identities.add(identity)
        paths.append(path)
        if len(paths) > max_entries:
            raise _invalid_git_output()
    return tuple(sorted(paths))


def _decode_lines(output: bytes) -> list[str]:
    try:
        return output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise _invalid_git_output() from None


def _decode_single_line(output: bytes) -> str:
    lines = _decode_lines(output)
    if len(lines) != 1:
        raise _invalid_git_output()
    return lines[0]


def _is_regular_unlinked_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not (attributes & reparse_flag)
    )


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _invalid_git_output() -> WorktreeGitError:
    return WorktreeGitError(
        WorktreeErrorCode.REPOSITORY_UNSUPPORTED,
        "Git returned invalid bounded output.",
    )
