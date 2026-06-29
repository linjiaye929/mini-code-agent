from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from pydantic import JsonValue

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.tools.registry import ToolRegistry


class EchoTool:
    def __init__(
        self,
        *,
        name: str = "echo",
        schema: Mapping[str, JsonValue] | None = None,
        result: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self._definition = ToolDefinition(
            name=name,
            description="Echo a validated string.",
            input_schema=schema
            or {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            side_effect=SideEffect.READ_ONLY,
        )
        self._result = result
        self._error = error
        self.calls: list[ToolCall] = []

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result  # type: ignore[return-value]
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                {"value": call.arguments["value"]},
                ensure_ascii=True,
                sort_keys=True,
            ),
        )


class CountingDefinitionTool(EchoTool):
    def __init__(self) -> None:
        super().__init__()
        self.definition_reads = 0

    @property
    def definition(self) -> ToolDefinition:
        self.definition_reads += 1
        return self._definition


def error_payload(result: ToolResult) -> dict[str, object]:
    return json.loads(result.content)  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_registry_validates_and_dispatches_known_tool() -> None:
    tool = EchoTool()
    registry = ToolRegistry([tool])
    call = ToolCall(id="call-1", name="echo", arguments={"value": "hello"})

    result = await registry.execute(call)

    assert result.tool_call_id == "call-1"
    assert result.is_error is False
    assert json.loads(result.content) == {"value": "hello"}
    assert tool.calls == [call]
    assert registry.definitions == (tool.definition,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"value": 1},
        {"value": "ok", "extra": True},
    ],
)
async def test_registry_rejects_invalid_arguments_before_execution(
    arguments: dict[str, JsonValue],
) -> None:
    tool = EchoTool()
    registry = ToolRegistry([tool])

    result = await registry.execute(ToolCall(id="call-1", name="echo", arguments=arguments))

    assert result.is_error is True
    assert error_payload(result)["error"] == {
        "code": "invalid_arguments",
        "message": "Tool arguments do not match the registered schema.",
    }
    assert tool.calls == []


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry([EchoTool()])

    result = await registry.execute(ToolCall(id="call-1", name="missing", arguments={}))

    assert result.tool_call_id == "call-1"
    assert result.is_error is True
    assert error_payload(result)["error"] == {
        "code": "unknown_tool",
        "message": "The requested tool is not registered.",
    }


def test_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="unique"):
        ToolRegistry([EchoTool(), EchoTool()])


def test_registry_rejects_invalid_json_schema() -> None:
    with pytest.raises(ValueError, match="invalid JSON Schema"):
        ToolRegistry(
            [
                EchoTool(
                    schema={
                        "type": "object",
                        "properties": {"value": {"type": "not-a-type"}},
                    }
                )
            ]
        )


def test_registry_snapshots_each_definition_once() -> None:
    tool = CountingDefinitionTool()

    registry = ToolRegistry([tool])

    assert tool.definition_reads == 1
    assert registry.definitions[0].name == "echo"


@pytest.mark.asyncio
async def test_registry_redacts_executor_exception() -> None:
    secret = "executor-secret-detail"
    registry = ToolRegistry([EchoTool(error=RuntimeError(secret))])

    result = await registry.execute(
        ToolCall(id="call-1", name="echo", arguments={"value": "hello"})
    )

    assert result.is_error is True
    assert error_payload(result)["error"] == {
        "code": "tool_failed",
        "message": "Tool execution failed.",
    }
    assert secret not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returned", "code"),
    [
        ("not-a-result", "invalid_tool_result"),
        (
            ToolResult(tool_call_id="other-id", content="wrong"),
            "invalid_tool_result",
        ),
    ],
)
async def test_registry_normalizes_invalid_executor_result(
    returned: object,
    code: str,
) -> None:
    registry = ToolRegistry([EchoTool(result=returned)])

    result = await registry.execute(
        ToolCall(id="call-1", name="echo", arguments={"value": "hello"})
    )

    assert result.tool_call_id == "call-1"
    assert result.is_error is True
    assert error_payload(result)["error"] == {
        "code": code,
        "message": "Tool returned an invalid result.",
    }
