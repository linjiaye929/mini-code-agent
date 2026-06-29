from __future__ import annotations

from mini_code_agent.checkpoint.fingerprint import tool_contract_sha256
from mini_code_agent.tools.base import SideEffect, ToolDefinition


def definition(
    name: str,
    *,
    side_effect: SideEffect = SideEffect.READ_ONLY,
    property_type: str = "string",
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": property_type}},
            "additionalProperties": False,
        },
        side_effect=side_effect,
    )


def test_tool_contract_fingerprint_is_order_independent() -> None:
    first = definition("first")
    second = definition("second")

    assert tool_contract_sha256((first, second)) == tool_contract_sha256((second, first))


def test_tool_contract_fingerprint_binds_schema_and_side_effect() -> None:
    baseline = tool_contract_sha256((definition("read"),))

    assert baseline != tool_contract_sha256((definition("read", property_type="integer"),))
    assert baseline != tool_contract_sha256((definition("read", side_effect=SideEffect.WRITE),))


def test_tool_contract_fingerprint_is_stable_sha256() -> None:
    fingerprint = tool_contract_sha256(())

    assert len(fingerprint) == 64
    assert set(fingerprint) <= set("0123456789abcdef")
