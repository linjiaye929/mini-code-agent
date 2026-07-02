from __future__ import annotations

from mini_code_agent.policy.models import TrustSource
from mini_code_agent.subagents.contracts import SubagentCompositionError
from mini_code_agent.subagents.models import SubagentProfile
from mini_code_agent.tools.base import SideEffect, ToolExecutor

_REQUIRED_IMPLEMENTATION_TOOLS = (
    "read_file",
    "search_text",
    "write_file",
    "edit_file",
)
_OPTIONAL_TEST_TOOL = "run_tests"
_EXPECTED_SIDE_EFFECTS = {
    "read_file": SideEffect.READ_ONLY,
    "search_text": SideEffect.READ_ONLY,
    "write_file": SideEffect.WRITE,
    "edit_file": SideEffect.WRITE,
    "run_tests": SideEffect.EXECUTE,
}


def validate_implementation_child_tools(
    profile: SubagentProfile,
    tools: ToolExecutor,
) -> None:
    try:
        definitions = tools.definitions
        names = tuple(definition.name for definition in definitions)
        accepted_names = {
            _REQUIRED_IMPLEMENTATION_TOOLS,
            (*_REQUIRED_IMPLEMENTATION_TOOLS, _OPTIONAL_TEST_TOOL),
        }
        if (
            profile.mode != "implementation"
            or profile.tool_names not in accepted_names
            or names != profile.tool_names
            or any(
                definition.side_effect is not _EXPECTED_SIDE_EFFECTS.get(definition.name)
                for definition in definitions
            )
            or getattr(tools, "governance_enforced", None) is not True
        ):
            raise SubagentCompositionError
        trust_source_for = getattr(tools, "trust_source_for", None)
        if not callable(trust_source_for):
            raise SubagentCompositionError
        if any(trust_source_for(name) is not TrustSource.SUBAGENT for name in names):
            raise SubagentCompositionError
    except SubagentCompositionError:
        raise
    except Exception:
        raise SubagentCompositionError from None
