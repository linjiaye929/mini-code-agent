from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from mini_code_agent.command.environment import build_minimal_environment
from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.command.models import CommandLimits, CommandRequest, CommandResult

_READ_CHUNK_BYTES = 64 * 1024


@dataclass(slots=True)
class _OutputBudget:
    limit: int
    used: int = 0
    exceeded: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def retain(self, chunk: bytes) -> tuple[bytes, bool]:
        async with self.lock:
            remaining = self.limit - self.used
            kept = chunk[:remaining]
            self.used += len(kept)
            truncated = len(kept) < len(chunk)
            if truncated:
                self.exceeded.set()
            return kept, truncated


@dataclass(slots=True)
class _CapturedStream:
    content: bytearray = field(default_factory=bytearray)
    truncated: bool = False


class CommandRunner:
    def __init__(
        self,
        *,
        limits: CommandLimits | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._limits = limits or CommandLimits()
        source_environment = os.environ if environment is None else environment
        self._environment = build_minimal_environment(source_environment)

    @property
    def limits(self) -> CommandLimits:
        return self._limits

    async def run(self, request: CommandRequest) -> CommandResult:
        if request.timeout_seconds > self._limits.max_timeout_seconds:
            raise CommandError(
                CommandErrorCode.INVALID_REQUEST,
                "Command request exceeds the configured timeout limit.",
            )

        started = time.monotonic()
        process = await self._start(request)
        stdout = _CapturedStream()
        stderr = _CapturedStream()
        budget = _OutputBudget(self._limits.max_output_bytes)
        readers = (
            asyncio.create_task(self._read_stream(process.stdout, stdout, budget)),
            asyncio.create_task(self._read_stream(process.stderr, stderr, budget)),
        )
        process_wait = asyncio.create_task(process.wait())
        output_wait = asyncio.create_task(budget.exceeded.wait())
        timed_out = False
        try:
            done, _ = await asyncio.wait(
                (process_wait, output_wait),
                timeout=request.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                await self._terminate_tree(process)
            elif output_wait in done and budget.exceeded.is_set():
                await self._terminate_tree(process)
            else:
                await process_wait
            await asyncio.gather(*readers)
            if budget.exceeded.is_set() and process.returncode is None:
                await self._terminate_tree(process)
        except asyncio.CancelledError:
            await self._cleanup_after_cancellation(process)
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            raise
        finally:
            output_wait.cancel()
            await asyncio.gather(output_wait, return_exceptions=True)
            if not process_wait.done():
                process_wait.cancel()
                await asyncio.gather(process_wait, return_exceptions=True)

        duration_ms = min(3_700_000, int((time.monotonic() - started) * 1000))
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd_display,
            exit_code=process.returncode,
            stdout=stdout.content.decode("utf-8", errors="replace"),
            stderr=stderr.content.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            output_limit_exceeded=budget.exceeded.is_set(),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            duration_ms=duration_ms,
        )

    async def _start(self, request: CommandRequest) -> asyncio.subprocess.Process:
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
                *request.argv,
                cwd=request.cwd,
                env=self._environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=start_new_session,
                creationflags=creation_flags,
            )
        except FileNotFoundError:
            raise CommandError(
                CommandErrorCode.COMMAND_NOT_FOUND,
                "Command executable was not found.",
            ) from None
        except OSError:
            raise CommandError(
                CommandErrorCode.COMMAND_START_FAILED,
                "Command process could not be started.",
            ) from None

    @staticmethod
    async def _read_stream(
        stream: asyncio.StreamReader | None,
        captured: _CapturedStream,
        budget: _OutputBudget,
    ) -> None:
        if stream is None:
            return
        while chunk := await stream.read(_READ_CHUNK_BYTES):
            if budget.exceeded.is_set():
                captured.truncated = True
                continue
            kept, truncated = await budget.retain(chunk)
            captured.content.extend(kept)
            if truncated:
                captured.truncated = True

    async def _cleanup_after_cancellation(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        cleanup = asyncio.create_task(self._terminate_tree(process))
        try:
            await asyncio.shield(cleanup)
        except (asyncio.CancelledError, CommandError):
            if not cleanup.done():
                await asyncio.gather(cleanup, return_exceptions=True)

    async def _terminate_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                await self._terminate_windows_tree(process)
            else:
                await self._terminate_posix_tree(process)
            async with asyncio.timeout(self._limits.cleanup_timeout_seconds):
                await process.wait()
        except (ProcessLookupError, ChildProcessError):
            return
        except (OSError, TimeoutError):
            try:
                process.kill()
                async with asyncio.timeout(self._limits.cleanup_timeout_seconds):
                    await process.wait()
            except ProcessLookupError:
                return
            except (OSError, TimeoutError):
                raise CommandError(
                    CommandErrorCode.COMMAND_CLEANUP_FAILED,
                    "Command process could not be terminated.",
                ) from None

    async def _terminate_windows_tree(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        system_root = self._environment.get("SYSTEMROOT")
        if not system_root:
            raise OSError("Windows system root is unavailable.")
        taskkill = os.path.join(system_root, "System32", "taskkill.exe")
        killer = await asyncio.create_subprocess_exec(
            taskkill,
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            env=self._environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        async with asyncio.timeout(self._limits.cleanup_timeout_seconds):
            await killer.wait()

    async def _terminate_posix_tree(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            async with asyncio.timeout(self._limits.cleanup_timeout_seconds / 2):
                await process.wait()
        except TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
