from pathlib import Path

import pytest
from pydantic import ValidationError

from mini_code_agent.command.models import CommandLimits, CommandRequest, CommandResult


def test_command_limits_have_bounded_defaults() -> None:
    limits = CommandLimits()

    assert limits.max_output_bytes == 1024 * 1024
    assert limits.max_timeout_seconds == 300
    assert limits.cleanup_timeout_seconds == 2.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_output_bytes", 0),
        ("max_output_bytes", 8 * 1024 * 1024 + 1),
        ("max_timeout_seconds", 0),
        ("max_timeout_seconds", 3601),
        ("cleanup_timeout_seconds", 0),
        ("cleanup_timeout_seconds", 11),
    ],
)
def test_command_limits_reject_invalid_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        CommandLimits.model_validate({field: value})


def test_command_request_is_immutable_and_bounded(tmp_path: Path) -> None:
    request = CommandRequest(
        argv=("python", "--version"),
        cwd=tmp_path,
        cwd_display=".",
        timeout_seconds=30,
    )

    assert request.argv == ("python", "--version")
    with pytest.raises(ValidationError):
        request.timeout_seconds = 31  # type: ignore[misc]


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("",),
        tuple("arg" for _ in range(65)),
        ("x" * 4097,),
    ],
)
def test_command_request_rejects_invalid_argv(
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        CommandRequest(
            argv=argv,
            cwd=tmp_path,
            cwd_display=".",
            timeout_seconds=30,
        )


def test_command_result_is_strict_and_immutable() -> None:
    result = CommandResult(
        argv=("python", "--version"),
        cwd=".",
        exit_code=0,
        stdout="Python\n",
        stderr="",
        timed_out=False,
        output_limit_exceeded=False,
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=12,
    )

    assert result.exit_code == 0
    with pytest.raises(ValidationError):
        CommandResult.model_validate({**result.model_dump(), "unexpected": True})
