"""Policy decisions for every tool call (AD-7).

``RulePolicy`` is the in-process implementation used by the MVP. The interface
is deliberately small so an OPA-backed policy can replace it without touching
the harness loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from factory.harness.tools import Tool
from factory.schemas import RiskTier


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    rule: str = ""


ALLOW = PolicyDecision(True)


@dataclass
class CallContext:
    agent: str
    risk_tier: RiskTier
    tainted: bool  # untrusted content entered the context within the taint window
    rel_path: str | None = None  # workspace-relative target of a file tool, resolved by the harness


class Policy(Protocol):
    def check(self, tool: Tool, args: dict[str, Any], ctx: CallContext) -> PolicyDecision: ...


DEFAULT_DENIED_COMMANDS: tuple[tuple[str, str], ...] = (
    (r"\bgit\s+push\b.*(--force\b|-f\b|--force-with-lease\b)", "force-push"),
    (r"\bgit\s+push\b.*\b(main|master)\b", "push-to-default-branch"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", "destructive-git"),
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|$)", "rm-root"),
    (r"(curl|wget)[^|;&]*\|\s*(ba|z)?sh\b", "pipe-to-shell"),
    (r"\b(sudo|su)\b", "privilege-escalation"),
    (r"\b(printenv|env)\b\s*($|\|)", "env-dump"),
)

DEFAULT_PROTECTED_PATHS: dict[RiskTier, tuple[str, ...]] = {
    # Paths an agent may write only when the Work Order's risk tier is at least this high.
    RiskTier.HIGH: (r"^\.github/workflows/", r"(^|/)Dockerfile$", r"^infra/", r"(^|/)migrations/"),
}

_TIER_ORDER = [RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH]


@dataclass
class RulePolicy:
    denied_commands: tuple[tuple[str, str], ...] = DEFAULT_DENIED_COMMANDS
    protected_paths: dict[RiskTier, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_PROTECTED_PATHS)
    )

    def check(self, tool: Tool, args: dict[str, Any], ctx: CallContext) -> PolicyDecision:
        if ctx.tainted and tool.sensitive:
            return PolicyDecision(
                False,
                f"{tool.name} is blocked while untrusted content is in recent context. Re-read "
                "trusted sources (the task, the repo) and decide again, or escalate.",
                "taint",
            )
        if tool.name == "run_command":
            cmd = str(args.get("command", ""))
            for pattern, rule in self.denied_commands:
                if re.search(pattern, cmd):
                    return PolicyDecision(False, f"command denied by policy ({rule})", rule)
        if tool.mutates and ctx.rel_path is not None:
            path = ctx.rel_path
            for min_tier, patterns in self.protected_paths.items():
                if _TIER_ORDER.index(ctx.risk_tier) >= _TIER_ORDER.index(min_tier):
                    continue
                for pattern in patterns:
                    if re.search(pattern, path):
                        return PolicyDecision(
                            False,
                            f"{path} is protected; changing it needs a {min_tier} risk Work Order. "
                            "Escalate if the change is required.",
                            "protected-path",
                        )
        return ALLOW
