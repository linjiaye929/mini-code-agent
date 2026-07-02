from __future__ import annotations

from typing import Literal

import pytest

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import TrustSource
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.models import SubagentProfile
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.worktrees.tools import validate_implementation_child_tools


def definition(name: str, side_effect: SideEffect) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Governed {name}.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect=side_effect,
    )


class StubGovernedTools:
    def __init__(
        self,
        definitions: tuple[ToolDefinition, ...],
        *,
        governed: object = True,
        trust_source: TrustSource = TrustSource.SUBAGENT,
    ) -> None:
        self._definitions = definitions
        self._governed = governed
        self._trust_source = trust_source
        self.results: dict[str, ToolResult] = {}

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    @property
    def governance_enforced(self) -> object:
        return self._governed

    def trust_source_for(self, tool_name: str) -> TrustSource:
        assert tool_name
        return self._trust_source

    async def execute(self, call: ToolCall) -> ToolResult:
        return self.results.get(
            call.id,
            ToolResult(tool_call_id=call.id, content="unused"),
        )


class LiteralGovernedTools(StubGovernedTools):
    @property
    def governance_enforced(self) -> Literal[True]:
        return True


def governed_tools(*, include_tests: bool = False) -> LiteralGovernedTools:
    definitions = (
        definition("read_file", SideEffect.READ_ONLY),
        definition("search_text", SideEffect.READ_ONLY),
        definition("write_file", SideEffect.WRITE),
        definition("edit_file", SideEffect.WRITE),
        *((definition("run_tests", SideEffect.EXECUTE),) if include_tests else ()),
    )
    return LiteralGovernedTools(definitions)


def implementation_profile(*, include_tests: bool = False) -> SubagentProfile:
    names = ("read_file", "search_text", "write_file", "edit_file")
    if include_tests:
        names = (*names, "run_tests")
    from mini_code_agent.agent.models import AgentLimits

    return SubagentProfile(
        profile_id="implementation",
        local_name="delegate_implementation",
        description="Implement one bounded task.",
        system_prompt="Change only files required by the task.",
        tool_names=names,
        mode="implementation",
        agent_limits=AgentLimits(max_turns=8, max_tool_calls=32),
    )


def test_implementation_tools_accept_exact_bounded_capabilities() -> None:
    validate_implementation_child_tools(
        implementation_profile(),
        governed_tools(),
    )
    validate_implementation_child_tools(
        implementation_profile(include_tests=True),
        governed_tools(include_tests=True),
    )


@pytest.mark.parametrize(
    ("profile", "tools"),
    [
        (
            None,
            governed_tools(),
        ),
        (
            implementation_profile(),
            LiteralGovernedTools(
                (
                    definition("read_file", SideEffect.READ_ONLY),
                    definition("search_text", SideEffect.READ_ONLY),
                    definition("write_file", SideEffect.WRITE),
                )
            ),
        ),
        (
            implementation_profile(),
            LiteralGovernedTools(
                (
                    *governed_tools().definitions,
                    definition("run_command", SideEffect.EXECUTE),
                )
            ),
        ),
        (
            implementation_profile(),
            LiteralGovernedTools(
                (
                    definition("read_file", SideEffect.READ_ONLY),
                    definition("search_text", SideEffect.READ_ONLY),
                    definition("write_file", SideEffect.WRITE),
                    definition("edit_file", SideEffect.READ_ONLY),
                )
            ),
        ),
        (
            implementation_profile(),
            StubGovernedTools(governed_tools().definitions, governed=False),
        ),
        (
            implementation_profile(),
            LiteralGovernedTools(
                governed_tools().definitions,
                trust_source=TrustSource.MODEL,
            ),
        ),
    ],
)
def test_implementation_tools_reject_mode_or_authority_drift(
    profile: SubagentProfile | None,
    tools: StubGovernedTools,
) -> None:
    if profile is None:
        profile = implementation_profile().model_copy(update={"mode": "analysis"})
    with pytest.raises(SubagentCompositionError):
        validate_implementation_child_tools(profile, tools)
