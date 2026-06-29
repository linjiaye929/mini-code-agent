from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mini_code_agent.command.models import CommandLimits
from mini_code_agent.command.runner import CommandRunner
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.run_command import RunCommandTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary


def command_call(
    argv: tuple[str, ...],
    *,
    cwd: str = ".",
    timeout_seconds: int = 5,
) -> ToolCall:
    return ToolCall(
        id="command-1",
        name="run_command",
        arguments={
            "argv": list(argv),
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
            "reason": "Run the requested verification command.",
        },
    )


def payload(content: str) -> dict[str, object]:
    return json.loads(content)  # type: ignore[no-any-return]


def tool_for(root: Path, *, limits: CommandLimits | None = None) -> RunCommandTool:
    return RunCommandTool(
        WorkspaceBoundary(root),
        CommandRunner(limits=limits),
    )


def test_run_command_definition_is_execute_and_closed_schema(tmp_path: Path) -> None:
    tool = tool_for(tmp_path)
    schema = tool.definition.model_dump(mode="json")["input_schema"]

    assert tool.definition.name == "run_command"
    assert tool.definition.side_effect is SideEffect.EXECUTE
    assert schema["required"] == ["argv", "reason"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["argv"]["maxItems"] == 64


@pytest.mark.asyncio
async def test_run_command_preview_exposes_exact_argv_and_relative_cwd(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "src"
    nested.mkdir()
    tool = tool_for(tmp_path)
    call = command_call((sys.executable, "-c", "print('ok')"), cwd="src")

    preview = await tool.preview(call)

    assert preview.risk is RiskLevel.CRITICAL
    assert preview.command == (sys.executable, "-c", "print('ok')")
    assert preview.resources == ("src",)
    assert preview.reason == "Run the requested verification command."


@pytest.mark.asyncio
async def test_run_command_executes_argv_without_shell_interpretation(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry([tool_for(tmp_path)])
    call = command_call(
        (
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            "; echo injected",
        )
    )

    result = await registry.execute(call)

    assert result.is_error is False
    body = payload(result.content)
    assert body["exit_code"] == 0
    assert body["stdout"] == f"; echo injected{os.linesep}"
    assert not (tmp_path / "injected").exists()


@pytest.mark.asyncio
async def test_run_command_direct_execution_validates_arguments(tmp_path: Path) -> None:
    tool = tool_for(tmp_path)
    call = ToolCall(
        id="command-1",
        name="run_command",
        arguments={"argv": [sys.executable, "--version"]},
    )

    result = await tool.execute(call)

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]


@pytest.mark.asyncio
async def test_run_command_rejects_unsafe_cwd_without_starting(tmp_path: Path) -> None:
    result = await tool_for(tmp_path).execute(
        command_call((sys.executable, "--version"), cwd="../outside")
    )

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "invalid_path"  # type: ignore[index]


@pytest.mark.asyncio
async def test_run_command_maps_missing_executable_to_static_error(tmp_path: Path) -> None:
    secret_name = str(tmp_path / "private-secret-command")

    result = await tool_for(tmp_path).execute(command_call((secret_name,)))

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "command_not_found"  # type: ignore[index]
    assert secret_name not in result.content


@pytest.mark.asyncio
async def test_run_command_rejects_timeout_above_runner_limit(tmp_path: Path) -> None:
    tool = tool_for(tmp_path, limits=CommandLimits(max_timeout_seconds=1))

    result = await tool.execute(command_call((sys.executable, "--version"), timeout_seconds=2))

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
