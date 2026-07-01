from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from mini_code_agent.agent.models import AgentLimits
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import TrustSource
from mini_code_agent.subagents.contracts import (
    SubagentCompositionError,
    SubagentProviderFactory,
    SubagentToolFactory,
    validate_child_tools,
)
from mini_code_agent.subagents.models import (
    SubagentLimits,
    SubagentProfile,
)
from mini_code_agent.tools.base import SideEffect, ToolDefinition


def profile_for() -> SubagentProfile:
    return SubagentProfile(
        profile_id="review",
        local_name="delegate_analysis",
        description="Run isolated review.",
        system_prompt="Review the assigned task.",
        tool_names=("read_file", "search_text"),
        agent_limits=AgentLimits(max_turns=4, max_tool_calls=8),
        limits=SubagentLimits(max_evidence_items=8),
    )


def definition(
    name: str,
    *,
    side_effect: SideEffect = SideEffect.READ_ONLY,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test {name}.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect=side_effect,
    )


class StubTools:
    def __init__(
        self,
        definitions: tuple[ToolDefinition, ...],
        *,
        governed: object = True,
        trust_source: TrustSource = TrustSource.SUBAGENT,
        trust_error: bool = False,
    ) -> None:
        self._definitions = definitions
        self._governed = governed
        self._trust_source = trust_source
        self._trust_error = trust_error

    @property
    def governance_enforced(self) -> object:
        return self._governed

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def trust_source_for(self, tool_name: str) -> TrustSource:
        if self._trust_error:
            raise RuntimeError("secret implementation failure")
        assert tool_name
        return self._trust_source

    async def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, content="unused")


def valid_tools() -> StubTools:
    return StubTools((definition("read_file"), definition("search_text")))


def test_validate_child_tools_accepts_exact_governed_subagent_contract() -> None:
    validate_child_tools(profile_for(), valid_tools())


@pytest.mark.parametrize(
    "tools",
    [
        StubTools(
            (
                definition("read_file"),
                definition("search_text"),
                definition("extra"),
            )
        ),
        StubTools((definition("read_file"),)),
        StubTools((definition("search_text"), definition("read_file"))),
        StubTools(
            (
                definition("read_file"),
                definition("search_text", side_effect=SideEffect.WRITE),
            )
        ),
        StubTools(
            (definition("read_file"), definition("search_text")),
            governed=False,
        ),
        StubTools(
            (definition("read_file"), definition("search_text")),
            governed=1,
        ),
        StubTools(
            (definition("read_file"), definition("search_text")),
            trust_source=TrustSource.MODEL,
        ),
        StubTools(
            (definition("read_file"), definition("search_text")),
            trust_error=True,
        ),
    ],
)
def test_validate_child_tools_rejects_authority_drift(tools: StubTools) -> None:
    with pytest.raises(SubagentCompositionError) as caught:
        validate_child_tools(profile_for(), tools)

    assert str(caught.value) == "Subagent capabilities did not match the host profile."
    assert "secret" not in str(caught.value)


def test_factory_protocols_define_bounded_inputs() -> None:
    class ProviderFactory:
        def create(self, profile: SubagentProfile, child_id: str) -> object:
            return (profile.profile_id, child_id)

    class ToolFactory:
        def create(self, profile: SubagentProfile, workspace_root: Path) -> StubTools:
            assert profile.profile_id == "review"
            assert workspace_root.is_absolute()
            return valid_tools()

    provider_factory: SubagentProviderFactory = ProviderFactory()  # type: ignore[assignment]
    tool_factory: SubagentToolFactory = ToolFactory()

    assert provider_factory is not None
    assert tool_factory.create(profile_for(), Path.cwd().resolve()).definitions


def test_governance_property_is_literal_true_in_public_protocol() -> None:
    class LiteralTools(StubTools):
        @property
        def governance_enforced(self) -> Literal[True]:
            return True

    validate_child_tools(
        profile_for(),
        LiteralTools((definition("read_file"), definition("search_text"))),
    )
