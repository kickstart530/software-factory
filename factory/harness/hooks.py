"""Extension points around tool calls and task completion."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from factory.harness.policy import PolicyDecision
from factory.harness.tools import Tool, ToolOutput

PreToolHook = Callable[[Tool, dict[str, Any]], PolicyDecision | None]
PostToolHook = Callable[[Tool, dict[str, Any], ToolOutput], ToolOutput]

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"AKIA[0-9A-Z]{16}", "aws-access-key"),
    (r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", "private-key"),
    (r"gh[pousr]_[A-Za-z0-9]{36,}", "github-token"),
    (r"sk-ant-[A-Za-z0-9_-]{20,}", "anthropic-key"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "slack-token"),
    # Secrets Broker minted credentials (cred_* pattern)
    (r"cred_[0-9a-f]{24}", "minted-credential-id"),
)

# Secret values minted by the Secrets Broker in this process. Redacted wherever
# they appear in tool or gate output, whatever their format.
_MINTED_SECRETS: set[str] = set()
_MIN_REDACTABLE_LEN = 8  # shorter values would redact ordinary text


def find_secrets(text: str) -> list[str]:
    found = [name for pattern, name in SECRET_PATTERNS if re.search(pattern, text)]
    if any(secret in text for secret in _MINTED_SECRETS):
        found.append("minted-credential")
    return found


def register_minted_secret(secret_value: str) -> None:
    """Register a minted secret value so it is redacted if it ever reaches output.

    Called by every SecretsBroker backend when it mints or reloads a credential.
    """
    if len(secret_value) >= _MIN_REDACTABLE_LEN:
        _MINTED_SECRETS.add(secret_value)


def redact_secrets(text: str) -> str:
    """Redact known secret patterns and every minted secret value from text."""
    # Exact values first, so a pattern match can't split one and leave part visible.
    for secret in sorted(_MINTED_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED:minted-credential]")
    for pattern, name in SECRET_PATTERNS:
        text = re.sub(pattern, f"[REDACTED:{name}]", text)
    return text


def secret_write_guard(tool: Tool, args: dict[str, Any]) -> PolicyDecision | None:
    """Refuse to write content that looks like a credential."""
    if not tool.mutates:
        return None
    found = find_secrets(str(args.get("content", "")) + str(args.get("new", "")))
    if found:
        return PolicyDecision(
            False, f"refusing to write what looks like a secret ({', '.join(found)})", "secret"
        )
    return None


def redact_output(tool: Tool, args: dict[str, Any], out: ToolOutput) -> ToolOutput:
    """Keep credentials that leak into tool output out of the transcript."""
    out.content = redact_secrets(out.content)
    return out


@dataclass
class Hooks:
    pre_tool: list[PreToolHook] = field(default_factory=lambda: [secret_write_guard])
    post_tool: list[PostToolHook] = field(default_factory=lambda: [redact_output])

    def run_pre(self, tool: Tool, args: dict[str, Any]) -> PolicyDecision | None:
        for hook in self.pre_tool:
            decision = hook(tool, args)
            if decision is not None and not decision.allowed:
                return decision
        return None

    def run_post(self, tool: Tool, args: dict[str, Any], out: ToolOutput) -> ToolOutput:
        for hook in self.post_tool:
            out = hook(tool, args, out)
        return out
