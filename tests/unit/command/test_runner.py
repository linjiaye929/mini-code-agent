from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.command.models import CommandLimits, CommandRequest
from mini_code_agent.command.runner import CommandRunner


def request(
    tmp_path: Path,
    code: str,
    *,
    timeout_seconds: int = 5,
) -> CommandRequest:
    return CommandRequest(
        argv=(sys.executable, "-c", code),
        cwd=tmp_path,
        cwd_display=".",
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_runner_captures_stdout_stderr_and_nonzero_exit(tmp_path: Path) -> None:
    runner = CommandRunner()

    result = await runner.run(
        request(
            tmp_path,
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
        )
    )

    assert result.exit_code == 7
    assert result.stdout == f"out{os.linesep}"
    assert result.stderr == f"err{os.linesep}"
    assert result.timed_out is False
    assert result.output_limit_exceeded is False
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_runner_uses_requested_cwd_and_stdin_eof(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    runner = CommandRunner()
    command = CommandRequest(
        argv=(
            sys.executable,
            "-c",
            "import pathlib,sys; print(pathlib.Path.cwd().name); print(repr(sys.stdin.read()))",
        ),
        cwd=nested,
        cwd_display="nested",
        timeout_seconds=5,
    )

    result = await runner.run(command)

    assert result.stdout == f"nested{os.linesep}''{os.linesep}"
    assert result.cwd == "nested"


@pytest.mark.asyncio
async def test_runner_decodes_malformed_utf8_with_replacement(tmp_path: Path) -> None:
    result = await CommandRunner().run(
        request(tmp_path, "import os; os.write(1, b'valid\\xfftext')")
    )

    assert result.stdout == "valid\ufffdtext"


@pytest.mark.asyncio
async def test_runner_does_not_inherit_arbitrary_or_secret_environment(
    tmp_path: Path,
) -> None:
    environment = {
        **os.environ,
        "OPENAI_API_KEY": "secret-value",
        "PROJECT_TOKEN": "another-secret",
    }
    result = await CommandRunner(environment=environment).run(
        request(
            tmp_path,
            "import os; print(os.getenv('OPENAI_API_KEY')); print(os.getenv('PROJECT_TOKEN'))",
        )
    )

    assert result.stdout == f"None{os.linesep}None{os.linesep}"
    assert "secret-value" not in result.model_dump_json()
    assert "another-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_runner_respects_explicit_empty_environment(tmp_path: Path) -> None:
    result = await CommandRunner(environment={}).run(
        request(tmp_path, "import os; print(os.getenv('PATH'))")
    )

    assert result.stdout == f"None{os.linesep}"


@pytest.mark.asyncio
async def test_runner_rejects_timeout_above_configured_limit(tmp_path: Path) -> None:
    runner = CommandRunner(limits=CommandLimits(max_timeout_seconds=1))

    with pytest.raises(CommandError) as captured:
        await runner.run(request(tmp_path, "print('never')", timeout_seconds=2))

    assert captured.value.code is CommandErrorCode.INVALID_REQUEST


@pytest.mark.asyncio
async def test_runner_normalizes_missing_executable(tmp_path: Path) -> None:
    missing = str(tmp_path / "private-secret-command")
    runner = CommandRunner()
    command = CommandRequest(
        argv=(missing,),
        cwd=tmp_path,
        cwd_display=".",
        timeout_seconds=5,
    )

    with pytest.raises(CommandError) as captured:
        await runner.run(command)

    assert captured.value.code is CommandErrorCode.COMMAND_NOT_FOUND
    assert missing not in captured.value.public_message
    assert missing not in str(captured.value)


@pytest.mark.asyncio
async def test_runner_timeout_terminates_without_late_side_effect(tmp_path: Path) -> None:
    marker = tmp_path / "late.txt"
    code = "import pathlib,time; time.sleep(5); pathlib.Path('late.txt').write_text('late')"
    runner = CommandRunner(limits=CommandLimits(cleanup_timeout_seconds=1))

    result = await runner.run(request(tmp_path, code, timeout_seconds=1))
    await asyncio.sleep(0.2)

    assert result.timed_out is True
    assert result.exit_code is not None
    assert not marker.exists()


@pytest.mark.asyncio
async def test_runner_timeout_terminates_grandchild(tmp_path: Path) -> None:
    grandchild_code = (
        "import pathlib,time; "
        "[(pathlib.Path('grandchild-heartbeat.txt').write_text(str(i)), time.sleep(.1)) "
        "for i in range(100)]"
    )
    parent_code = (
        "import pathlib,subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        "pathlib.Path('grandchild-started.txt').write_text('started'); "
        "time.sleep(30)"
    )
    runner = CommandRunner(limits=CommandLimits(cleanup_timeout_seconds=2))

    result = await runner.run(request(tmp_path, parent_code, timeout_seconds=1))
    heartbeat = tmp_path / "grandchild-heartbeat.txt"
    assert heartbeat.exists()
    value_after_cleanup = heartbeat.read_text(encoding="utf-8")
    await asyncio.sleep(0.5)

    assert result.timed_out is True
    assert (tmp_path / "grandchild-started.txt").exists()
    assert heartbeat.read_text(encoding="utf-8") == value_after_cleanup


@pytest.mark.asyncio
async def test_runner_output_limit_terminates_and_truncates(tmp_path: Path) -> None:
    runner = CommandRunner(limits=CommandLimits(max_output_bytes=64, cleanup_timeout_seconds=1))

    result = await runner.run(request(tmp_path, "import os; os.write(1, b'x' * 10000)"))

    assert result.output_limit_exceeded is True
    assert result.stdout_truncated is True
    assert len(result.stdout.encode()) <= 64
    assert result.exit_code is not None


@pytest.mark.asyncio
async def test_runner_cancellation_cleans_process_before_reraising(tmp_path: Path) -> None:
    marker = tmp_path / "cancelled-late.txt"
    code = (
        "import pathlib,time; time.sleep(5); pathlib.Path('cancelled-late.txt').write_text('late')"
    )
    runner = CommandRunner(limits=CommandLimits(cleanup_timeout_seconds=1))
    task = asyncio.create_task(runner.run(request(tmp_path, code)))
    await asyncio.sleep(0.2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)

    assert not marker.exists()
