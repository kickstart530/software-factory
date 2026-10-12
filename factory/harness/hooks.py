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
)


def find_secrets(text: str) -> list[str]:
    return [name for pattern, name in SECRET_PATTERNS if re.search(pattern, text)]


def redact_secrets(text: str) -> str:
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
