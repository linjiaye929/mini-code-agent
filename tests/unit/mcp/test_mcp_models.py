from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import JsonValue, SecretStr, ValidationError

from mini_code_agent.mcp.models import (
    MCP_PROTOCOL_VERSION,
    McpCallError,
    McpCallErrorCode,
    McpConnectionApprovalRequest,
    McpConnectionError,
    McpConnectionErrorCode,
    McpInitializeSnapshot,
    McpLifecycleState,
    McpLimits,
    McpRemoteTool,
    McpServerProfile,
    McpToolGrant,
    McpToolPage,
)
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import SideEffect

PYTHON_EXECUTABLE = str(Path(sys.executable).resolve())


def grant_for(
    *,
    remote_name: str = "status",
    local_name: str = "mcp_status",
) -> McpToolGrant:
    return McpToolGrant(
        remote_name=remote_name,
        local_name=local_name,
        description="Read a host-reviewed status value.",
        side_effect=SideEffect.READ_ONLY,
        risk=RiskLevel.LOW,
        input_schema_sha256="a" * 64,
    )


def profile_for(
    tmp_path: Path,
    *,
    grants: tuple[McpToolGrant, ...] | None = None,
    environment: dict[str, SecretStr] | None = None,
) -> McpServerProfile:
    return McpServerProfile(
        server_id="local-test",
        command=PYTHON_EXECUTABLE,
        args=("-m", "example_server"),
        cwd=tmp_path.resolve(),
        environment=environment or {},
        expected_server_name="mini-code-agent-test",
        expected_server_version="1.0.0",
        grants=grants or (grant_for(),),
    )


def test_protocol_and_lifecycle_are_explicit() -> None:
    assert MCP_PROTOCOL_VERSION == "2025-11-25"
    assert {item.value for item in McpLifecycleState} == {
        "new",
        "approving",
        "connecting",
        "verifying",
        "ready",
        "failed",
        "closing",
        "closed",
    }


def test_grant_is_exact_frozen_host_authority() -> None:
    grant = grant_for()

    assert grant.remote_name == "status"
    assert grant.local_name == "mcp_status"
    assert grant.side_effect is SideEffect.READ_ONLY
    assert grant.risk is RiskLevel.LOW
    with pytest.raises(ValidationError):
        grant_for(remote_name="contains whitespace")
    with pytest.raises(ValidationError):
        grant_for(local_name="MCP.Status")
    with pytest.raises(ValidationError):
        grant.model_copy(update={"input_schema_sha256": "not-a-hash"}).model_validate(
            grant.model_dump() | {"input_schema_sha256": "not-a-hash"}
        )


def test_profile_masks_secrets_and_projects_public_approval(tmp_path: Path) -> None:
    profile = profile_for(
        tmp_path,
        environment={"API_TOKEN": SecretStr("do-not-leak")},
    )

    assert "do-not-leak" not in repr(profile)
    assert "do-not-leak" not in profile.model_dump_json()
    request = profile.approval_request()
    assert request == McpConnectionApprovalRequest(
        server_id="local-test",
        command=(PYTHON_EXECUTABLE, "-m", "example_server"),
        cwd=str(tmp_path.resolve()),
        environment_keys=("API_TOKEN",),
    )
    assert "do-not-leak" not in request.model_dump_json()
    assert "operating-system privileges" in request.warning


def test_profile_fixture_uses_unlinked_real_interpreter(tmp_path: Path) -> None:
    profile = profile_for(tmp_path)

    assert profile.command == PYTHON_EXECUTABLE
    assert not Path(profile.command).is_symlink()


def test_profile_freezes_environment_and_grants(tmp_path: Path) -> None:
    environment = {"API_TOKEN": SecretStr("one")}
    grants = [grant_for()]
    profile = McpServerProfile.model_validate(
        {
            "server_id": "local-test",
            "command": PYTHON_EXECUTABLE,
            "args": ("-m", "example_server"),
            "cwd": tmp_path.resolve(),
            "environment": environment,
            "expected_server_name": "mini-code-agent-test",
            "expected_server_version": "1.0.0",
            "grants": grants,
        }
    )

    environment["OTHER"] = SecretStr("two")
    grants.append(grant_for(remote_name="other", local_name="mcp_other"))
    assert tuple(profile.environment) == ("API_TOKEN",)
    assert tuple(item.remote_name for item in profile.grants) == ("status",)
    with pytest.raises(TypeError):
        profile.environment["OTHER"] = SecretStr("two")  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", ""),
        ("command", "python\x00evil"),
        ("args", ("",)),
        ("args", ("safe", "bad\x00arg")),
        ("environment", {"BAD-KEY": SecretStr("value")}),
        ("environment", {"TOKEN": SecretStr("bad\x00value")}),
        ("expected_protocol_version", "2024-11-05"),
        ("expected_server_name", "bad\x00name"),
    ],
)
def test_profile_rejects_unsafe_tokens(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = profile_for(tmp_path).model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)


def test_profile_requires_existing_absolute_unlinked_directory(tmp_path: Path) -> None:
    payload = profile_for(tmp_path).model_dump()
    payload["cwd"] = Path("relative")
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)

    payload["cwd"] = tmp_path / "missing"
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)

    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    payload["cwd"] = file_path
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)


def test_profile_requires_absolute_existing_unlinked_executable(
    tmp_path: Path,
) -> None:
    payload = profile_for(tmp_path).model_dump()
    payload["command"] = "python"
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)

    payload["command"] = str(tmp_path / "missing.exe")
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)

    payload["command"] = str(tmp_path)
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)


def test_profile_rejects_linked_executable_when_supported(
    tmp_path: Path,
) -> None:
    linked = tmp_path / "linked-python.exe"
    try:
        linked.symlink_to(PYTHON_EXECUTABLE)
    except OSError as exc:
        pytest.skip(f"Executable symlink unavailable in this environment: {exc}")

    payload = profile_for(tmp_path).model_dump()
    payload["command"] = str(linked)
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)


def test_profile_rejects_linked_working_directory_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlink unavailable in this environment: {exc}")

    payload = profile_for(tmp_path).model_dump()
    payload["cwd"] = linked
    with pytest.raises(ValidationError):
        McpServerProfile.model_validate(payload)


def test_profile_rejects_duplicate_remote_or_local_grants(tmp_path: Path) -> None:
    duplicate_remote = (
        grant_for(),
        grant_for(remote_name="status", local_name="mcp_other"),
    )
    with pytest.raises(ValidationError):
        profile_for(tmp_path, grants=duplicate_remote)

    duplicate_local = (
        grant_for(),
        grant_for(remote_name="other", local_name="mcp_status"),
    )
    with pytest.raises(ValidationError):
        profile_for(tmp_path, grants=duplicate_local)


def test_limits_are_hard_bounded() -> None:
    limits = McpLimits()
    assert limits.call_timeout_seconds == 30.0
    assert limits.max_tools == 32

    with pytest.raises(ValidationError):
        McpLimits(call_timeout_seconds=301)
    with pytest.raises(ValidationError):
        McpLimits(max_result_bytes=1_048_577)


def test_snapshots_freeze_json_contracts() -> None:
    input_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    tool = McpRemoteTool(
        name="status",
        input_schema=input_schema,
        output_schema={"type": "object"},
    )
    page = McpToolPage(tools=(tool,))
    initialized = McpInitializeSnapshot(
        protocol_version=MCP_PROTOCOL_VERSION,
        server_name="mini-code-agent-test",
        server_version="1.0.0",
        has_tools=True,
        tools_list_changed=False,
    )

    input_schema["type"] = "array"
    assert tool.input_schema["type"] == "object"
    assert page.tools[0] is tool
    assert initialized.has_tools is True
    assert json.loads(tool.model_dump_json())["input_schema"]["type"] == "object"
    with pytest.raises(TypeError):
        tool.input_schema["type"] = "array"  # type: ignore[index]


def test_public_errors_have_static_bounded_messages() -> None:
    connection = McpConnectionError(McpConnectionErrorCode.IDENTITY_MISMATCH)
    call = McpCallError(McpCallErrorCode.RESULT_UNSUPPORTED)

    assert str(connection) == "MCP server identity did not match the approved profile."
    assert str(call) == "MCP tool returned an unsupported result."
    assert set(McpConnectionErrorCode) >= {
        McpConnectionErrorCode.CONNECTION_NOT_APPROVED,
        McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH,
    }
    assert set(McpCallErrorCode) >= {
        McpCallErrorCode.NOT_CONNECTED,
        McpCallErrorCode.COMPLETION_UNKNOWN,
    }
    assert all(len(str(error)) <= 300 for error in (connection, call))


def test_approval_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        McpConnectionApprovalRequest.model_validate(
            {
                "server_id": "local-test",
                "command": (PYTHON_EXECUTABLE,),
                "cwd": os.getcwd(),
                "environment_keys": (),
                "secret": "leak",
            }
        )
