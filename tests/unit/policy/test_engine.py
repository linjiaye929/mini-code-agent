import pytest
from pydantic import ValidationError

from mini_code_agent.policy.engine import PolicyEngine
from mini_code_agent.policy.models import (
    PolicyDecision,
    PolicyRequest,
    PolicyRule,
    RiskLevel,
    SessionMode,
    TrustSource,
)
from mini_code_agent.tools.base import SideEffect


def request(
    *,
    tool_name: str = "write_file",
    side_effect: SideEffect = SideEffect.WRITE,
    risk: RiskLevel = RiskLevel.HIGH,
    resources: tuple[str, ...] = ("src/app.py",),
    command: tuple[str, ...] = (),
    session_mode: SessionMode = SessionMode.INTERACTIVE,
    trust_source: TrustSource = TrustSource.MODEL,
) -> PolicyRequest:
    return PolicyRequest(
        tool_name=tool_name,
        side_effect=side_effect,
        risk=risk,
        resources=resources,
        command=command,
        session_mode=session_mode,
        trust_source=trust_source,
    )


def test_policy_enums_are_stable() -> None:
    assert {item.value for item in PolicyDecision} == {"allow", "ask", "deny"}
    assert {item.value for item in RiskLevel} == {
        "low",
        "medium",
        "high",
        "critical",
    }
    assert {item.value for item in SessionMode} == {
        "interactive",
        "non_interactive",
    }
    assert {item.value for item in TrustSource} == {
        "user",
        "project",
        "model",
        "extension",
        "subagent",
    }


@pytest.mark.parametrize(
    ("side_effect", "decision", "rule_id"),
    [
        (SideEffect.READ_ONLY, PolicyDecision.ALLOW, "default-read-only"),
        (SideEffect.WRITE, PolicyDecision.ASK, "default-write"),
        (SideEffect.EXECUTE, PolicyDecision.DENY, "default-execute"),
        (SideEffect.NETWORK, PolicyDecision.DENY, "default-network"),
    ],
)
def test_secure_defaults(
    side_effect: SideEffect,
    decision: PolicyDecision,
    rule_id: str,
) -> None:
    result = PolicyEngine().evaluate(request(side_effect=side_effect))

    assert result.decision is decision
    assert result.rule_id == rule_id
    assert result.rationale


def test_first_matching_custom_rule_wins() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="deny-generated",
                decision=PolicyDecision.DENY,
                rationale="Generated files are protected.",
                tool_glob="write_*",
                resource_glob="generated/*",
            ),
            PolicyRule(
                id="allow-writes",
                decision=PolicyDecision.ALLOW,
                rationale="Workspace writes are pre-approved.",
                tool_glob="write_*",
                side_effect=SideEffect.WRITE,
            ),
        )
    )

    result = engine.evaluate(request(resources=("generated/output.py",)))

    assert result.decision is PolicyDecision.DENY
    assert result.rule_id == "deny-generated"


def test_rule_matches_every_resource_not_only_one() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="allow-src",
                decision=PolicyDecision.ALLOW,
                rationale="Only source paths are approved.",
                resource_glob="src/*",
            ),
        )
    )

    result = engine.evaluate(request(resources=("src/app.py", "secrets/key.txt")))

    assert result.decision is PolicyDecision.ASK
    assert result.rule_id == "default-write"


def test_rule_matches_session_and_trust_source() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="allow-user-interactive",
                decision=PolicyDecision.ALLOW,
                rationale="User-originated interactive write.",
                session_mode=SessionMode.INTERACTIVE,
                trust_source=TrustSource.USER,
            ),
        )
    )

    allowed = engine.evaluate(request(trust_source=TrustSource.USER))
    model_request = engine.evaluate(request())

    assert allowed.rule_id == "allow-user-interactive"
    assert model_request.rule_id == "default-write"


def test_rule_can_match_subagent_without_matching_parent_model() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="allow-subagent-read",
                decision=PolicyDecision.ALLOW,
                rationale="Bounded child reads are allowed.",
                side_effect=SideEffect.READ_ONLY,
                trust_source=TrustSource.SUBAGENT,
            ),
        )
    )

    child = engine.evaluate(
        request(
            side_effect=SideEffect.READ_ONLY,
            trust_source=TrustSource.SUBAGENT,
        )
    )
    parent = engine.evaluate(
        request(
            side_effect=SideEffect.READ_ONLY,
            trust_source=TrustSource.MODEL,
        )
    )

    assert child.rule_id == "allow-subagent-read"
    assert parent.rule_id == "default-read-only"


def test_rule_matches_side_effect_and_tool_glob() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="ask-edits",
                decision=PolicyDecision.ASK,
                rationale="Edits require review.",
                tool_glob="edit_*",
                side_effect=SideEffect.WRITE,
            ),
        )
    )

    matching = engine.evaluate(request(tool_name="edit_file"))
    other = engine.evaluate(request(tool_name="write_file"))

    assert matching.rule_id == "ask-edits"
    assert other.rule_id == "default-write"


def test_rule_can_narrow_execute_permission_by_executable() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="ask-python",
                decision=PolicyDecision.ASK,
                rationale="Python commands require approval.",
                tool_glob="run_command",
                side_effect=SideEffect.EXECUTE,
                executable_glob="*python*",
            ),
        )
    )

    python = engine.evaluate(
        request(
            tool_name="run_command",
            side_effect=SideEffect.EXECUTE,
            risk=RiskLevel.CRITICAL,
            command=("C:/Python/python.exe", "-m", "pytest"),
        )
    )
    powershell = engine.evaluate(
        request(
            tool_name="run_command",
            side_effect=SideEffect.EXECUTE,
            risk=RiskLevel.CRITICAL,
            command=("powershell.exe", "-Command", "pytest"),
        )
    )
    missing = engine.evaluate(
        request(
            tool_name="run_command",
            side_effect=SideEffect.EXECUTE,
            risk=RiskLevel.CRITICAL,
        )
    )

    assert python.rule_id == "ask-python"
    assert powershell.rule_id == "default-execute"
    assert missing.rule_id == "default-execute"


def test_result_does_not_retain_raw_request() -> None:
    secret = "secret-resource-name"
    result = PolicyEngine().evaluate(request(resources=(f"src/{secret}.txt",)))

    assert secret not in result.model_dump_json()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": ""},
        {"id": "contains space"},
        {"rationale": ""},
        {"tool_glob": ""},
        {"resource_glob": ""},
        {"executable_glob": ""},
    ],
)
def test_rule_rejects_invalid_bounded_fields(kwargs: dict[str, str]) -> None:
    values: dict[str, object] = {
        "id": "rule-1",
        "decision": PolicyDecision.DENY,
        "rationale": "Denied by test rule.",
    }
    values.update(kwargs)

    with pytest.raises(ValidationError):
        PolicyRule.model_validate(values)


def test_policy_snapshots_rules() -> None:
    rules = [
        PolicyRule(
            id="deny-write",
            decision=PolicyDecision.DENY,
            rationale="Writes denied.",
        )
    ]
    engine = PolicyEngine(rules=rules)
    rules.clear()

    assert engine.rules[0].id == "deny-write"
