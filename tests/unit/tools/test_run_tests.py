from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from mini_code_agent.command.errors import CommandError, CommandErrorCode
from mini_code_agent.command.models import CommandRequest, CommandResult
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.testing.models import PytestLimits, PytestProfile
from mini_code_agent.testing.pytest_runner import PytestRunner
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.run_tests import RunTestsTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary


class RecordingCommandRunner:
    def __init__(self, *, error: CommandError | None = None) -> None:
        self.error = error
        self.requests: list[CommandRequest] = []

    async def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        report_path = Path(
            next(
                argument.removeprefix("--junitxml=")
                for argument in request.argv
                if argument.startswith("--junitxml=")
            )
        )
        report_path.write_text('<testsuite name="empty" />', encoding="utf-8")
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd_display,
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            output_limit_exceeded=False,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=10,
        )


def call_for(
    targets: list[str] | None = None,
    *,
    name: str = "run_tests",
    reason: str = "Verify the changed behavior.",
) -> ToolCall:
    arguments = cast(dict[str, JsonValue], {"reason": reason})
    if targets is not None:
        arguments["targets"] = cast(JsonValue, targets)
    return ToolCall(id="tests-1", name=name, arguments=arguments)


def payload(content: str) -> dict[str, object]:
    return json.loads(content)  # type: ignore[no-any-return]


def tool_for(
    root: Path,
    *,
    command_runner: RecordingCommandRunner | None = None,
    default_targets: tuple[str, ...] = (),
    limits: PytestLimits | None = None,
) -> tuple[RunTestsTool, RecordingCommandRunner]:
    active_command_runner = command_runner or RecordingCommandRunner()
    profile = PytestProfile(
        python_executable=Path(sys.executable).resolve(),
        default_targets=default_targets,
        timeout_seconds=30,
        max_failures=3,
    )
    runner = PytestRunner(
        root,
        profile=profile,
        limits=limits,
        command_runner=active_command_runner,
    )
    return (
        RunTestsTool(WorkspaceBoundary(root), runner),
        active_command_runner,
    )


def test_run_tests_definition_is_execute_with_closed_bounded_schema(
    tmp_path: Path,
) -> None:
    tool, _ = tool_for(tmp_path)
    schema = tool.definition.model_dump(mode="json")["input_schema"]

    assert tool.definition.name == "run_tests"
    assert tool.definition.side_effect is SideEffect.EXECUTE
    assert schema["required"] == ["reason"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["targets"]["maxItems"] == 32
    assert schema["properties"]["targets"]["items"]["maxLength"] == 1024


@pytest.mark.asyncio
async def test_preview_exposes_fixed_command_and_validated_resources(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    test_file = tmp_path / "tests" / "test_api.py"
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    tool, _ = tool_for(tmp_path)

    preview = await tool.preview(
        call_for(["tests/unit", "tests/test_api.py"]),
    )

    assert preview.risk is RiskLevel.CRITICAL
    assert preview.resources == ("tests/unit", "tests/test_api.py")
    assert preview.reason == "Verify the changed behavior."
    assert preview.command is not None
    assert preview.command[:4] == (
        str(Path(sys.executable).resolve()),
        "-I",
        "-m",
        "pytest",
    )
    assert preview.command[-3:] == ("--", "tests/unit", "tests/test_api.py")
    assert "--junitxml=<managed-junit-report.xml>" in preview.command


@pytest.mark.asyncio
async def test_execute_uses_host_default_targets_only_when_omitted(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests").mkdir()
    tool, command_runner = tool_for(tmp_path, default_targets=("tests",))

    result = await tool.execute(call_for())

    assert result.is_error is False
    assert command_runner.requests[0].argv[-2:] == ("--", "tests")
    body = payload(result.content)
    assert body["status"] == "passed"
    assert body["report_status"] == "complete"


@pytest.mark.asyncio
async def test_execute_accepts_existing_file_and_directory_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_one.py").write_text("", encoding="utf-8")
    tool, command_runner = tool_for(tmp_path)

    result = await tool.execute(call_for(["tests", "tests/test_one.py"]))

    assert result.is_error is False
    assert command_runner.requests[0].argv[-3:] == (
        "--",
        "tests",
        "tests/test_one.py",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected_code"),
    [
        ("../outside", "invalid_path"),
        ("/absolute", "invalid_path"),
        (r"C:\absolute", "invalid_path"),
        (".git/config", "invalid_path"),
        ("missing.py", "not_found"),
        ("tests/test_api.py::test_one", "invalid_path"),
        ("-k", "invalid_path"),
        ("tests/-option.py", "invalid_path"),
    ],
)
async def test_execute_rejects_unsafe_or_missing_targets_without_starting(
    tmp_path: Path,
    target: str,
    expected_code: str,
) -> None:
    (tmp_path / "tests").mkdir()
    tool, command_runner = tool_for(tmp_path)

    result = await tool.execute(call_for([target]))

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == expected_code  # type: ignore[index]
    assert command_runner.requests == []


@pytest.mark.asyncio
async def test_execute_rejects_duplicate_resolved_targets(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    tool, command_runner = tool_for(tmp_path)

    result = await tool.execute(call_for(["tests", "tests"]))

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert command_runner.requests == []


@pytest.mark.asyncio
async def test_execute_enforces_runtime_target_limit(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"target-{index}").mkdir()
    tool, command_runner = tool_for(
        tmp_path,
        limits=PytestLimits(max_targets=2),
    )

    result = await tool.execute(
        call_for(["target-0", "target-1", "target-2"]),
    )

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert command_runner.requests == []


@pytest.mark.asyncio
async def test_registry_rejects_empty_or_more_than_32_targets(tmp_path: Path) -> None:
    tool, command_runner = tool_for(tmp_path)
    registry = ToolRegistry([tool])

    empty = await registry.execute(call_for([]))
    too_many = await registry.execute(call_for(["target"] * 33))

    assert empty.is_error is True
    assert too_many.is_error is True
    assert payload(empty.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert payload(too_many.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert command_runner.requests == []


@pytest.mark.asyncio
async def test_direct_execution_rejects_wrong_name_or_missing_reason(
    tmp_path: Path,
) -> None:
    tool, command_runner = tool_for(tmp_path)
    wrong_name = await tool.execute(call_for(name="other_tool"))
    missing_reason = await tool.execute(
        ToolCall(id="tests-2", name="run_tests", arguments={}),
    )

    assert wrong_name.is_error is True
    assert missing_reason.is_error is True
    assert payload(wrong_name.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert payload(missing_reason.content)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert command_runner.requests == []


@pytest.mark.asyncio
async def test_execute_maps_command_error_without_leaking_executable(
    tmp_path: Path,
) -> None:
    secret = str(tmp_path / "private-python")
    command_runner = RecordingCommandRunner(
        error=CommandError(
            CommandErrorCode.COMMAND_NOT_FOUND,
            f"missing {secret}",
        ),
    )
    tool, _ = tool_for(tmp_path, command_runner=command_runner)

    result = await tool.execute(call_for())

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "command_not_found"  # type: ignore[index]
    assert secret not in result.content


def test_tool_rejects_runner_for_different_workspace(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    runner = PytestRunner(
        other,
        profile=PytestProfile(python_executable=Path(sys.executable).resolve()),
        command_runner=RecordingCommandRunner(),
    )

    with pytest.raises(ValueError, match="workspace"):
        RunTestsTool(WorkspaceBoundary(tmp_path), runner)


@pytest.mark.asyncio
async def test_execute_rejects_directory_symlink_when_available(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlink unavailable in this environment: {exc}")
    tool, command_runner = tool_for(tmp_path)

    result = await tool.execute(call_for(["link"]))

    assert result.is_error is True
    assert payload(result.content)["error"]["code"] == "link_traversal"  # type: ignore[index]
    assert command_runner.requests == []


def test_target_identity_is_case_normalized_on_windows(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows-specific path identity behavior.")
    (tmp_path / "Tests").mkdir()
    tool, _ = tool_for(tmp_path)

    with pytest.raises(ValueError, match="duplicate"):
        tool.prepare_targets(("Tests", "tests"))
