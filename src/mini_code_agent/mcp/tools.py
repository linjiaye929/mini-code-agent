from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.mcp.contracts import VerifiedMcpTool
from mini_code_agent.mcp.models import (
    McpCallError,
    McpCallErrorCode,
    McpCallResult,
    McpLifecycleState,
    McpServerProfile,
    McpToolGrant,
)
from mini_code_agent.policy.models import ActionPreview
from mini_code_agent.tools.base import ToolDefinition


class _OutputValidator(Protocol):
    def is_valid(self, instance: object) -> bool: ...


class McpToolClient(Protocol):
    @property
    def profile(self) -> McpServerProfile: ...

    @property
    def state(self) -> McpLifecycleState: ...

    @property
    def verified_tools(self) -> tuple[VerifiedMcpTool, ...]: ...

    async def call(
        self,
        grant: McpToolGrant,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult: ...


class McpTool:
    def __init__(
        self,
        client: McpToolClient,
        verified: VerifiedMcpTool,
    ) -> None:
        self._client = client
        self._verified = verified
        self._output_validator: _OutputValidator | None = None
        if verified.output_schema is not None:
            plain_schema = _bounded_plain_json(
                verified.output_schema,
                max_depth=client.profile.limits.max_json_depth,
                max_nodes=client.profile.limits.max_json_nodes,
                max_string_chars=client.profile.limits.max_text_chars,
            )
            if not isinstance(plain_schema, Mapping):
                raise ValueError("Verified MCP output schema must be an object.")
            self._output_validator = cast(
                _OutputValidator,
                Draft202012Validator(cast(Mapping[str, object], plain_schema)),
            )

    @property
    def definition(self) -> ToolDefinition:
        return self._verified.definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        grant = self._verified.grant
        return ActionPreview(
            tool_call_id=call.id,
            tool_name=self._verified.definition.name,
            side_effect=grant.side_effect,
            risk=grant.risk,
            summary=f"Call approved MCP Tool {grant.local_name}.",
            reason="The model requested a host-approved MCP Tool.",
            resources=(f"mcp://{self._client.profile.server_id}/tools/{grant.remote_name}",),
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._verified.definition.name:
            return _error(
                call.id,
                "unknown_tool",
                "The requested MCP Tool is not registered.",
            )
        try:
            result = await self._client.call(
                self._verified.grant,
                call.arguments,
            )
            return self._normalize(call.id, result)
        except asyncio.CancelledError:
            raise
        except McpCallError as exc:
            return _error(call.id, exc.code.value, str(exc))
        except Exception:
            return _error(
                call.id,
                McpCallErrorCode.FAILED.value,
                str(McpCallError(McpCallErrorCode.FAILED)),
            )

    def _normalize(
        self,
        call_id: str,
        result: McpCallResult,
    ) -> ToolResult:
        limits = self._client.profile.limits
        if (
            len(result.text) > limits.max_text_blocks
            or sum(len(item) for item in result.text) > limits.max_text_chars
        ):
            raise McpCallError(McpCallErrorCode.RESULT_TOO_LARGE)

        structured: JsonValue | None = None
        if result.structured_content is not None:
            try:
                structured = _bounded_plain_json(
                    result.structured_content,
                    max_depth=limits.max_json_depth,
                    max_nodes=limits.max_json_nodes,
                    max_string_chars=limits.max_text_chars,
                )
            except _JsonLimitError:
                raise McpCallError(McpCallErrorCode.RESULT_TOO_LARGE) from None
            except (TypeError, ValueError, OverflowError):
                raise McpCallError(McpCallErrorCode.RESULT_INVALID) from None

        if (
            not result.is_error
            and self._output_validator is not None
            and (structured is None or not self._output_validator.is_valid(structured))
        ):
            raise McpCallError(McpCallErrorCode.RESULT_INVALID)

        payload: dict[str, JsonValue] = {
            "content_type": "mcp_tool_result",
            "server_id": self._client.profile.server_id,
            "text": list(result.text),
            "tool": self._verified.grant.remote_name,
        }
        if structured is not None:
            payload["structured_content"] = structured
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            raise McpCallError(McpCallErrorCode.RESULT_INVALID) from None
        if len(encoded) > limits.max_result_bytes:
            raise McpCallError(McpCallErrorCode.RESULT_TOO_LARGE)
        return ToolResult(
            tool_call_id=call_id,
            content=encoded.decode("ascii"),
            is_error=result.is_error,
        )


def build_mcp_tools(client: McpToolClient) -> tuple[McpTool, ...]:
    if client.state is not McpLifecycleState.READY:
        raise ValueError("MCP client must be ready before building tools.")
    return tuple(McpTool(client, verified) for verified in client.verified_tools)


class _JsonLimitError(ValueError):
    pass


def _bounded_plain_json(
    value: object,
    *,
    max_depth: int,
    max_nodes: int,
    max_string_chars: int,
) -> JsonValue:
    nodes = 0

    def convert(item: object, depth: int) -> JsonValue:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise _JsonLimitError
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError
            return item
        if isinstance(item, str):
            if len(item) > max_string_chars:
                raise _JsonLimitError
            return item
        if isinstance(item, Mapping):
            mapping = cast(Mapping[object, object], item)
            converted: dict[str, JsonValue] = {}
            for key, nested in mapping.items():
                if not isinstance(key, str):
                    raise TypeError
                if len(key) > 1024:
                    raise _JsonLimitError
                converted[key] = convert(nested, depth + 1)
            return converted
        if isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            sequence = cast(Sequence[object], item)
            return [convert(nested, depth + 1) for nested in sequence]
        raise TypeError

    return convert(value, 1)


def _error(call_id: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=json.dumps(
            {"error": {"code": code, "message": message}},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        is_error=True,
    )
