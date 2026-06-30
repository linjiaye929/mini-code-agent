from typing import TYPE_CHECKING, Any

from mini_code_agent.policy.approval import (
    ApprovalHandler,
    DenyAllApprovalHandler,
    StaticApprovalHandler,
)
from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.models import (
    ActionGuard,
    ActionGuardResult,
    ActionPreview,
    AllowAllActionGuard,
    ApprovalRequest,
    PolicyDecision,
    PolicyRequest,
    PolicyResult,
    PolicyRule,
    RiskLevel,
    SessionMode,
    TrustSource,
)

if TYPE_CHECKING:
    from mini_code_agent.policy.executor import GovernedToolExecutor

__all__ = [
    "ActionGuard",
    "ActionGuardResult",
    "ActionPreview",
    "AllowAllActionGuard",
    "ApprovalHandler",
    "ApprovalRequest",
    "DenyAllApprovalHandler",
    "GovernedToolExecutor",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyRequest",
    "PolicyResult",
    "PolicyRule",
    "RiskLevel",
    "SessionMode",
    "StaticApprovalHandler",
    "TrustSource",
]


def __getattr__(name: str) -> Any:
    if name == "GovernedToolExecutor":
        from mini_code_agent.policy.executor import GovernedToolExecutor

        return GovernedToolExecutor
    raise AttributeError(name)
