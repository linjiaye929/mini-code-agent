from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase

from mini_code_agent.policy.models import (
    PolicyDecision,
    PolicyRequest,
    PolicyResult,
    PolicyRule,
)
from mini_code_agent.tools.base import SideEffect


class PolicyEngine:
    def __init__(self, rules: Iterable[PolicyRule] = ()) -> None:
        self._rules = tuple(rules)

    @property
    def rules(self) -> tuple[PolicyRule, ...]:
        return self._rules

    def evaluate(self, request: PolicyRequest) -> PolicyResult:
        for rule in self._rules:
            if self._matches(rule, request):
                return PolicyResult(
                    decision=rule.decision,
                    rule_id=rule.id,
                    rationale=rule.rationale,
                )
        return _default_result(request.side_effect)

    @staticmethod
    def _matches(rule: PolicyRule, request: PolicyRequest) -> bool:
        if not fnmatchcase(request.tool_name, rule.tool_glob):
            return False
        if rule.side_effect is not None and request.side_effect is not rule.side_effect:
            return False
        if rule.risk is not None and request.risk is not rule.risk:
            return False
        if rule.session_mode is not None and request.session_mode is not rule.session_mode:
            return False
        if rule.trust_source is not None and request.trust_source is not rule.trust_source:
            return False
        return rule.resource_glob is None or (
            bool(request.resources)
            and all(fnmatchcase(resource, rule.resource_glob) for resource in request.resources)
        )


def _default_result(side_effect: SideEffect) -> PolicyResult:
    if side_effect is SideEffect.READ_ONLY:
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            rule_id="default-read-only",
            rationale="Read-only tools are allowed by default.",
        )
    if side_effect is SideEffect.WRITE:
        return PolicyResult(
            decision=PolicyDecision.ASK,
            rule_id="default-write",
            rationale="Write tools require approval by default.",
        )
    if side_effect is SideEffect.EXECUTE:
        return PolicyResult(
            decision=PolicyDecision.DENY,
            rule_id="default-execute",
            rationale="Execute tools are denied by default.",
        )
    return PolicyResult(
        decision=PolicyDecision.DENY,
        rule_id="default-network",
        rationale="Network tools are denied by default.",
    )
