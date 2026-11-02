"""Enhanced policy engine with OPA support and approval integration (Phase 2b).

This module extends the RulePolicy from policy.py with:
1. External policy evaluation (OPA via CLI)
2. Approval-required marking for high-risk operations
3. Policy evaluation logging for audit

Policy stack (evaluated in order):
1. RulePolicy hard denials (force-push, rm -rf /, etc.)
2. OPA policy evaluation (if configured)
3. Approval requirements (if OPA or rules say so)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.harness.policy import CallContext, Policy, PolicyDecision, RulePolicy
from factory.harness.tools import Tool
from factory.schemas import PolicyEvaluationInput, PolicyEvaluationResult


@dataclass
class EnhancedPolicyDecision:
    """Policy decision with approval requirement."""

    allowed: bool
    reason: str = ""
    rule: str = ""
    requires_approval: bool = False
    approval_reason: str = ""

    @classmethod
    def from_policy_decision(
        cls, pd: PolicyDecision, requires_approval: bool = False, approval_reason: str = ""
    ) -> EnhancedPolicyDecision:
        return cls(
            allowed=pd.allowed,
            reason=pd.reason,
            rule=pd.rule,
            requires_approval=requires_approval,
            approval_reason=approval_reason,
        )


class OPAPolicy:
    """OPA-based policy evaluator via CLI.

    Expects `opa eval` to be available in PATH, or a custom command.
    Policy bundle is loaded from a directory containing .rego files.
    """

    def __init__(
        self,
        policy_dir: Path,
        entrypoint: str = "data.factory.allow",
        opa_command: str = "opa",
    ):
        self.policy_dir = policy_dir
        self.entrypoint = entrypoint
        self.opa_command = opa_command

    def evaluate(
        self, tool: Tool, args: dict[str, Any], ctx: CallContext
    ) -> PolicyEvaluationResult:
        """Evaluate policy via OPA CLI."""
        # Build input for OPA
        policy_input = PolicyEvaluationInput(
            agent=ctx.agent,
            tool_name=tool.name,
            tool_args=args,
            risk_tier=ctx.risk_tier,
            work_order_id="unknown",  # Harness should inject this
            task_id="unknown",  # Harness should inject this
            tainted=ctx.tainted,
            rel_path=ctx.rel_path,
        )

        input_json = policy_input.model_dump_json()

        try:
            # Call: opa eval -d <policy_dir> -i <input> --format json <entrypoint>
            result = subprocess.run(
                [
                    self.opa_command,
                    "eval",
                    "-d",
                    str(self.policy_dir),
                    "-I",
                    input_json,
                    "--format",
                    "json",
                    self.entrypoint,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                # OPA evaluation failed - default deny for safety
                return PolicyEvaluationResult(
                    allowed=False,
                    reason=f"OPA evaluation failed: {result.stderr}",
                    rule="opa-error",
                )

            # Parse OPA result
            output = json.loads(result.stdout)
            # OPA returns {"result": [{"expressions": [{"value": <result>}]}]}
            opa_result = output.get("result", [{}])[0].get("expressions", [{}])[0].get("value")

            if isinstance(opa_result, bool):
                # Simple boolean result
                return PolicyEvaluationResult(
                    allowed=opa_result,
                    reason="" if opa_result else "denied by OPA policy",
                    rule="opa",
                )
            elif isinstance(opa_result, dict):
                # Structured result with approval requirements
                return PolicyEvaluationResult(
                    allowed=opa_result.get("allow", False),
                    reason=opa_result.get("reason", ""),
                    rule=opa_result.get("rule", "opa"),
                    requires_approval=opa_result.get("requires_approval", False),
                    approval_reason=opa_result.get("approval_reason", ""),
                )
            else:
                return PolicyEvaluationResult(
                    allowed=False,
                    reason=f"unexpected OPA result type: {type(opa_result)}",
                    rule="opa-error",
                )

        except subprocess.TimeoutExpired:
            return PolicyEvaluationResult(
                allowed=False, reason="OPA evaluation timeout", rule="opa-timeout"
            )
        except Exception as e:
            return PolicyEvaluationResult(
                allowed=False, reason=f"OPA evaluation error: {e}", rule="opa-error"
            )


class EnhancedPolicy:
    """Policy engine that combines RulePolicy with optional OPA and approval requirements.

    Evaluation order:
    1. RulePolicy (hard denials)
    2. OPA policy (if configured)
    3. Approval marking

    If RulePolicy denies, the operation is blocked immediately.
    If OPA denies, the operation is blocked.
    If either requires approval, the harness pauses for human decision.
    """

    def __init__(
        self,
        rule_policy: RulePolicy | None = None,
        opa_policy: OPAPolicy | None = None,
        approval_required_tools: set[str] | None = None,
    ):
        self.rule_policy = rule_policy or RulePolicy()
        self.opa_policy = opa_policy
        self.approval_required_tools = approval_required_tools or set()

    def check(self, tool: Tool, args: dict[str, Any], ctx: CallContext) -> EnhancedPolicyDecision:
        """Evaluate policy and determine if approval is needed."""

        # 1. Check RulePolicy first (hard denials)
        rule_decision = self.rule_policy.check(tool, args, ctx)
        if not rule_decision.allowed:
            # Hard denial - no approval can override
            return EnhancedPolicyDecision.from_policy_decision(rule_decision)

        # 2. Check OPA policy if configured
        requires_approval = False
        approval_reason = ""

        if self.opa_policy:
            opa_result = self.opa_policy.evaluate(tool, args, ctx)
            if not opa_result.allowed:
                # OPA denies - could be approval-overridable
                if opa_result.requires_approval:
                    requires_approval = True
                    approval_reason = opa_result.approval_reason or opa_result.reason
                else:
                    # Hard OPA denial
                    return EnhancedPolicyDecision(
                        allowed=False, reason=opa_result.reason, rule=opa_result.rule
                    )
            elif opa_result.requires_approval:
                # OPA allows but requires approval
                requires_approval = True
                approval_reason = opa_result.approval_reason or "operation requires approval"

        # 3. Check tool-level approval requirements
        if tool.name in self.approval_required_tools:
            requires_approval = True
            if not approval_reason:
                approval_reason = f"{tool.name} requires human approval"

        return EnhancedPolicyDecision(
            allowed=True,
            requires_approval=requires_approval,
            approval_reason=approval_reason,
        )


# Convenience for backwards compatibility
def make_policy(
    rule_policy: RulePolicy | None = None,
    opa_policy_dir: Path | None = None,
    approval_required_tools: set[str] | None = None,
) -> Policy | EnhancedPolicy:
    """Factory function to create policy based on configuration."""
    if not opa_policy_dir and not approval_required_tools:
        # Just use RulePolicy
        return rule_policy or RulePolicy()

    opa_policy = OPAPolicy(opa_policy_dir) if opa_policy_dir else None
    return EnhancedPolicy(
        rule_policy=rule_policy,
        opa_policy=opa_policy,
        approval_required_tools=approval_required_tools,
    )
