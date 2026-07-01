from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Protocol, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from mini_code_agent.domain.json import (
    FrozenJsonValue,
    freeze_json_mapping,
    thaw_json_mapping,
)
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import SideEffect

MCP_PROTOCOL_VERSION = "2025-11-25"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ServerId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
CommandToken = Annotated[str, Field(min_length=1, max_length=4096)]
EnvironmentName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"),
]


class McpLifecycleState(StrEnum):
    NEW = "new"
    APPROVING = "approving"
    CONNECTING = "connecting"
    VERIFYING = "verifying"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


class McpToolGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    remote_name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    local_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    side_effect: SideEffect
    risk: RiskLevel
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256 | None = None


class McpLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_timeout_seconds: float = Field(default=60.0, ge=0.1, le=300.0)
    startup_timeout_seconds: float = Field(default=15.0, ge=0.1, le=60.0)
    list_timeout_seconds: float = Field(default=10.0, ge=0.1, le=60.0)
    call_timeout_seconds: float = Field(default=30.0, ge=0.1, le=300.0)
    close_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    max_tools: int = Field(default=32, ge=1, le=128)
    max_schema_bytes: int = Field(default=65_536, ge=2, le=262_144)
    max_result_bytes: int = Field(default=262_144, ge=64, le=1_048_576)
    max_text_blocks: int = Field(default=32, ge=1, le=128)
    max_text_chars: int = Field(default=131_072, ge=1, le=524_288)
    max_json_depth: int = Field(default=16, ge=1, le=64)
    max_json_nodes: int = Field(default=10_000, ge=1, le=100_000)


_APPROVAL_WARNING = "This starts local code with the Agent user's operating-system privileges."


class McpConnectionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: ServerId
    command: tuple[CommandToken, ...] = Field(min_length=1, max_length=65)
    cwd: str = Field(min_length=1, max_length=4096)
    environment_keys: tuple[EnvironmentName, ...] = Field(default=(), max_length=32)
    warning: Literal[
        "This starts local code with the Agent user's operating-system privileges."
    ] = _APPROVAL_WARNING


class McpConnectionApprover(Protocol):
    async def approve(self, request: McpConnectionApprovalRequest) -> bool: ...


class McpServerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: ServerId
    command: CommandToken
    args: tuple[CommandToken, ...] = Field(default=(), max_length=64)
    cwd: Path
    environment: Mapping[EnvironmentName, SecretStr] = Field(default_factory=dict)
    expected_protocol_version: Literal["2025-11-25"] = MCP_PROTOCOL_VERSION
    expected_server_name: str = Field(min_length=1, max_length=128)
    expected_server_version: str = Field(min_length=1, max_length=128)
    grants: tuple[McpToolGrant, ...] = Field(min_length=1, max_length=32)
    limits: McpLimits = Field(default_factory=McpLimits)

    @field_validator("command", "expected_server_name", "expected_server_version")
    @classmethod
    def reject_nul_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("MCP profile text cannot contain NUL.")
        return value

    @field_validator("command")
    @classmethod
    def require_safe_executable(cls, value: str) -> str:
        _require_safe_regular_file(Path(value), label="executable")
        return value

    @field_validator("args")
    @classmethod
    def reject_nul_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in item for item in value):
            raise ValueError("MCP command arguments cannot contain NUL.")
        return value

    @field_validator("cwd")
    @classmethod
    def require_safe_working_directory(cls, value: Path) -> Path:
        _require_safe_directory(value)
        return value

    @field_validator("environment")
    @classmethod
    def freeze_environment(
        cls,
        value: Mapping[str, SecretStr],
    ) -> Mapping[str, SecretStr]:
        if len(value) > 32:
            raise ValueError("MCP environment cannot contain more than 32 entries.")
        copied: dict[str, SecretStr] = {}
        for key, secret in value.items():
            secret_value = secret.get_secret_value()
            if "\x00" in secret_value or len(secret_value) > 8192:
                raise ValueError("MCP environment values must be bounded and NUL-free.")
            copied[key] = secret
        return MappingProxyType(copied)

    @field_serializer("environment")
    def serialize_environment(
        self,
        value: Mapping[str, SecretStr],
    ) -> dict[str, SecretStr]:
        return dict(value)

    @model_validator(mode="after")
    def require_unique_grants(self) -> Self:
        remote_names = tuple(item.remote_name for item in self.grants)
        local_names = tuple(item.local_name for item in self.grants)
        if len(remote_names) != len(set(remote_names)):
            raise ValueError("MCP remote Tool grants must be unique.")
        if len(local_names) != len(set(local_names)):
            raise ValueError("MCP local Tool aliases must be unique.")
        if len(self.grants) > self.limits.max_tools:
            raise ValueError("MCP grants exceed the configured Tool limit.")
        return self

    def approval_request(self) -> McpConnectionApprovalRequest:
        return McpConnectionApprovalRequest(
            server_id=self.server_id,
            command=(self.command, *self.args),
            cwd=os.fspath(self.cwd),
            environment_keys=tuple(sorted(self.environment)),
        )

    def revalidate_launch_paths(self) -> None:
        _require_safe_regular_file(Path(self.command), label="executable")
        _require_safe_directory(self.cwd)


class McpInitializeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(min_length=1, max_length=32)
    server_name: str = Field(min_length=1, max_length=128)
    server_version: str = Field(min_length=1, max_length=128)
    has_tools: bool
    tools_list_changed: bool = False


class McpRemoteTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue] | None = None
    task_support: Literal["forbidden", "optional", "required"] = "forbidden"

    @model_validator(mode="after")
    def freeze_schemas(self) -> Self:
        object.__setattr__(self, "input_schema", freeze_json_mapping(self.input_schema))
        if self.output_schema is not None:
            object.__setattr__(
                self,
                "output_schema",
                freeze_json_mapping(self.output_schema),
            )
        return self

    @field_serializer("input_schema")
    def serialize_input_schema(
        self,
        value: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = cast(Mapping[str, FrozenJsonValue], value)
        return thaw_json_mapping(frozen)

    @field_serializer("output_schema")
    def serialize_output_schema(
        self,
        value: Mapping[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        frozen = cast(Mapping[str, FrozenJsonValue], value)
        return thaw_json_mapping(frozen)


class McpToolPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: tuple[McpRemoteTool, ...] = Field(default=(), max_length=128)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=1024)


class McpCallResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: tuple[str, ...] = ()
    structured_content: Mapping[str, JsonValue] | None = None
    is_error: bool = False

    @model_validator(mode="after")
    def freeze_structured_content(self) -> Self:
        if self.structured_content is not None:
            object.__setattr__(
                self,
                "structured_content",
                freeze_json_mapping(self.structured_content),
            )
        return self

    @field_serializer("structured_content")
    def serialize_structured_content(
        self,
        value: Mapping[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        frozen = cast(Mapping[str, FrozenJsonValue], value)
        return thaw_json_mapping(frozen)


class McpConnectionErrorCode(StrEnum):
    CONNECTION_NOT_APPROVED = "connection_not_approved"
    CONNECTION_TIMEOUT = "connection_timeout"
    CONNECTION_FAILED = "connection_failed"
    IDENTITY_MISMATCH = "identity_mismatch"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    TOOLS_CAPABILITY_MISSING = "tools_capability_missing"
    DYNAMIC_TOOLS_UNSUPPORTED = "dynamic_tools_unsupported"
    TOOL_CONTRACT_MISMATCH = "tool_contract_mismatch"
    TOOL_SCHEMA_INVALID = "tool_schema_invalid"
    TOOL_LISTING_TOO_LARGE = "tool_listing_too_large"
    UNSUPPORTED_SERVER_FEATURE = "unsupported_server_feature"
    CLOSE_FAILED = "close_failed"


_CONNECTION_MESSAGES = {
    McpConnectionErrorCode.CONNECTION_NOT_APPROVED: "MCP server connection was not approved.",
    McpConnectionErrorCode.CONNECTION_TIMEOUT: "MCP server connection timed out.",
    McpConnectionErrorCode.CONNECTION_FAILED: "MCP server connection failed.",
    McpConnectionErrorCode.IDENTITY_MISMATCH: (
        "MCP server identity did not match the approved profile."
    ),
    McpConnectionErrorCode.PROTOCOL_MISMATCH: (
        "MCP protocol version did not match the approved profile."
    ),
    McpConnectionErrorCode.TOOLS_CAPABILITY_MISSING: (
        "MCP server did not provide the required Tools capability."
    ),
    McpConnectionErrorCode.DYNAMIC_TOOLS_UNSUPPORTED: ("Dynamic MCP Tool lists are not supported."),
    McpConnectionErrorCode.TOOL_CONTRACT_MISMATCH: (
        "MCP Tool contracts did not match the approved profile."
    ),
    McpConnectionErrorCode.TOOL_SCHEMA_INVALID: "MCP Tool schema is invalid.",
    McpConnectionErrorCode.TOOL_LISTING_TOO_LARGE: "MCP Tool listing exceeded a limit.",
    McpConnectionErrorCode.UNSUPPORTED_SERVER_FEATURE: (
        "MCP server requires an unsupported feature."
    ),
    McpConnectionErrorCode.CLOSE_FAILED: "MCP server could not be closed cleanly.",
}


class McpConnectionError(RuntimeError):
    def __init__(self, code: McpConnectionErrorCode) -> None:
        self.code = code
        super().__init__(_CONNECTION_MESSAGES[code])


class McpCallErrorCode(StrEnum):
    NOT_CONNECTED = "mcp_not_connected"
    TIMEOUT = "mcp_tool_timeout"
    FAILED = "mcp_tool_failed"
    RESULT_INVALID = "mcp_tool_result_invalid"
    RESULT_TOO_LARGE = "mcp_tool_result_too_large"
    RESULT_UNSUPPORTED = "mcp_tool_result_unsupported"
    COMPLETION_UNKNOWN = "mcp_tool_completion_unknown"


_CALL_MESSAGES = {
    McpCallErrorCode.NOT_CONNECTED: "MCP server is not connected.",
    McpCallErrorCode.TIMEOUT: "MCP tool call timed out.",
    McpCallErrorCode.FAILED: "MCP tool call failed.",
    McpCallErrorCode.RESULT_INVALID: "MCP tool returned an invalid result.",
    McpCallErrorCode.RESULT_TOO_LARGE: "MCP tool result exceeded a configured limit.",
    McpCallErrorCode.RESULT_UNSUPPORTED: "MCP tool returned an unsupported result.",
    McpCallErrorCode.COMPLETION_UNKNOWN: (
        "MCP tool completion is unknown; a side effect may have occurred."
    ),
}


class McpCallError(RuntimeError):
    def __init__(self, code: McpCallErrorCode) -> None:
        self.code = code
        super().__init__(_CALL_MESSAGES[code])


def _is_reparse_point(status: os.stat_result) -> bool:
    file_attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(file_attributes & reparse_flag)


def _require_safe_regular_file(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"MCP {label} path must be absolute.")
    try:
        status = path.lstat()
    except OSError:
        raise ValueError(f"MCP {label} is unavailable.") from None
    if (
        path.is_symlink()
        or _is_reparse_point(status)
        or not stat.S_ISREG(status.st_mode)
        or not os.access(path, os.X_OK)
    ):
        raise ValueError(f"MCP {label} must be an executable unlinked regular file.")


def _require_safe_directory(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("MCP working directory must be absolute.")
    try:
        status = path.lstat()
    except OSError:
        raise ValueError("MCP working directory is unavailable.") from None
    if path.is_symlink() or _is_reparse_point(status) or not stat.S_ISDIR(status.st_mode):
        raise ValueError("MCP working directory must be an unlinked directory.")
