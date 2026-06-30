import json
from typing import cast

import pytest

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.runtime_info import RuntimeInfoTool


def test_runtime_info_is_declared_read_only() -> None:
    tool = RuntimeInfoTool()

    assert len(tool.definitions) == 1
    assert tool.definitions[0].name == "runtime_info"
    assert tool.definitions[0].side_effect is SideEffect.READ_ONLY


def test_runtime_info_schema_cannot_be_mutated_globally() -> None:
    tool = RuntimeInfoTool()
    schema = tool.definitions[0].input_schema

    with pytest.raises(TypeError):
        cast(dict[str, object], schema)["additionalProperties"] = True

    assert RuntimeInfoTool().definitions[0].input_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_runtime_info_returns_safe_structured_data() -> None:
    tool = RuntimeInfoTool()

    result = await tool.execute(ToolCall(id="call-1", name="runtime_info", arguments={}))

    payload = json.loads(result.content)
    assert result.tool_call_id == "call-1"
    assert result.is_error is False
    assert payload["package_version"] == "0.13.0a0"
    assert payload["python_version"]
    assert payload["platform"]


@pytest.mark.asyncio
async def test_runtime_info_rejects_unknown_tool_without_raising() -> None:
    tool = RuntimeInfoTool()

    result = await tool.execute(ToolCall(id="call-2", name="unknown_tool", arguments={}))

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "unknown_tool"


@pytest.mark.asyncio
async def test_runtime_info_rejects_unexpected_arguments() -> None:
    tool = RuntimeInfoTool()

    result = await tool.execute(
        ToolCall(id="call-3", name="runtime_info", arguments={"secret": "value"})
    )

    assert result.is_error is True
    assert "value" not in result.content
