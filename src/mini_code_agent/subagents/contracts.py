from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from mini_code_agent.policy.models import TrustSource
from mini_code_agent.providers.base import ModelProvider
from mini_code_agent.subagents.models import SubagentProfile
from mini_code_agent.tools.base import SideEffect, ToolExecutor


class SubagentProviderFactory(Protocol):
    def create(
        self,
        profile: SubagentProfile,
        child_id: str,
    ) -> ModelProvider: ...


class SubagentToolFactory(Protocol):
    def create(
        self,
        profile: SubagentProfile,
        workspace_root: Path,
    ) -> ToolExecutor: ...


class GovernedSubagentTools(ToolExecutor, Protocol):
    @property
    def governance_enforced(self) -> Literal[True]: ...

    def trust_source_for(self, tool_name: str) -> TrustSource: ...


class SubagentCompositionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Subagent capabilities did not match the host profile.")


def validate_child_tools(
    profile: SubagentProfile,
    tools: ToolExecutor,
) -> None:
    try:
        definitions = tools.definitions
        names = tuple(definition.name for definition in definitions)
        if names != profile.tool_names:
            raise SubagentCompositionError
        if any(definition.side_effect is not SideEffect.READ_ONLY for definition in definitions):
            raise SubagentCompositionError
        if getattr(tools, "governance_enforced", None) is not True:
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
