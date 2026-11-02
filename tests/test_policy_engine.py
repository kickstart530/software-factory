"""Tests for the enhanced policy engine (Phase 2b)."""

from pathlib import Path

from pydantic import BaseModel

from factory.harness.policy import RulePolicy
from factory.harness.policy_engine import EnhancedPolicy, EnhancedPolicyDecision, OPAPolicy
from factory.harness.tools import Tool, ToolContext, ToolOutput
from factory.schemas import RiskTier


def _make_tool(name: str, mutates: bool = False, sensitive: bool = False) -> Tool:
    """Helper to create a test tool."""

    class DummyInput(BaseModel):
        pass

    def handler(ctx: ToolContext, args: DummyInput) -> ToolOutput:
        return ToolOutput(content="ok")

    return Tool(
        name=name,
        description=f"Test tool {name}",
        input_model=DummyInput,
        handler=handler,
        mutates=mutates,
        sensitive=sensitive,
    )


def test_enhanced_policy_delegates_to_rule_policy():
    """Rule policy denials are honored."""
    rule_policy = RulePolicy()
    enhanced = EnhancedPolicy(rule_policy=rule_policy)

    tool = _make_tool("run_command", mutates=True, sensitive=False)
    args = {"command": "git push --force"}
    ctx = type(
        "CallContext",
        (),
        {
            "agent": "implementer",
            "risk_tier": RiskTier.LOW,
            "tainted": False,
            "rel_path": None,
        },
    )()

    decision = enhanced.check(tool, args, ctx)
    assert not decision.allowed
    assert "force-push" in decision.reason or "force-push" in decision.rule


def test_enhanced_policy_marks_approval_required_tools():
    """Tools in approval_required_tools need approval."""
    enhanced = EnhancedPolicy(approval_required_tools={"deploy", "delete_database"})

    tool = _make_tool("deploy", mutates=True, sensitive=False)
    args = {"target": "production"}
    ctx = type(
        "CallContext",
        (),
        {
            "agent": "implementer",
            "risk_tier": RiskTier.LOW,
            "tainted": False,
            "rel_path": None,
        },
    )()

    decision = enhanced.check(tool, args, ctx)
    assert decision.allowed  # Not denied
    assert decision.requires_approval
    assert "approval" in decision.approval_reason.lower()


def test_enhanced_policy_allows_normal_operations():
    """Normal operations pass through."""
    enhanced = EnhancedPolicy()

    tool = _make_tool("read_file", mutates=False, sensitive=False)
    args = {"path": "README.md"}
    ctx = type(
        "CallContext",
        (),
        {
            "agent": "implementer",
            "risk_tier": RiskTier.LOW,
            "tainted": False,
            "rel_path": "README.md",
        },
    )()

    decision = enhanced.check(tool, args, ctx)
    assert decision.allowed
    assert not decision.requires_approval


def test_enhanced_policy_blocks_tainted_sensitive():
    """Taint rules from RulePolicy are enforced."""
    rule_policy = RulePolicy()
    enhanced = EnhancedPolicy(rule_policy=rule_policy)

    tool = _make_tool("run_command", mutates=True, sensitive=True)
    args = {"command": "echo test"}
    ctx = type(
        "CallContext",
        (),
        {
            "agent": "implementer",
            "risk_tier": RiskTier.LOW,
            "tainted": True,  # Untrusted content in context
            "rel_path": None,
        },
    )()

    decision = enhanced.check(tool, args, ctx)
    assert not decision.allowed
    assert "taint" in decision.reason.lower() or "untrusted" in decision.reason.lower()


def test_enhanced_policy_decision_dataclass():
    """EnhancedPolicyDecision can be created and used."""
    decision = EnhancedPolicyDecision(
        allowed=True,
        reason="",
        rule="test",
        requires_approval=True,
        approval_reason="high-risk operation",
    )
    assert decision.allowed
    assert decision.requires_approval
    assert decision.approval_reason == "high-risk operation"


def test_opa_policy_returns_default_on_missing_opa():
    """OPAPolicy handles missing OPA binary gracefully."""
    # This test will fail if 'opa' is in PATH but not configured
    # In real usage, the factory would check for OPA before creating OPAPolicy
    opa = OPAPolicy(policy_dir=Path("/nonexistent"), opa_command="opa_not_installed")

    tool = _make_tool("test", mutates=False, sensitive=False)
    args = {}
    ctx = type(
        "CallContext",
        (),
        {
            "agent": "implementer",
            "risk_tier": RiskTier.LOW,
            "tainted": False,
            "rel_path": None,
        },
    )()

    result = opa.evaluate(tool, args, ctx)
    # Should fail safely (deny)
    assert not result.allowed
    assert "error" in result.reason.lower() or "failed" in result.reason.lower()
