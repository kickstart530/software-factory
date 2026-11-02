# ADR-0006: External actions go through a tool gateway (CLI-first)

- **Status:** Accepted
- **Date:** 2026-10-12
- **Updated:** 2026-11-02 (removed MCP, refocused on CLI-first tooling)

## Context

Agents need to act on git hosts, CI, trackers and cloud APIs. Giving each agent credentials directly spreads secrets into contexts and sandboxes.

## Decision

External tools are exposed through a tool gateway that authenticates the calling task, applies policy, injects credentials server-side and writes the audit log. Credentials never appear in model context.

**CLI-first approach:** Tools are implemented as CLI commands (extending the `factory` CLI or wrapping existing CLIs like `gh`, `git`, `kubectl`). The gateway routes tool calls to these CLIs with injected credentials, captures output, and applies policy.

This avoids protocol lock-in and leverages existing battle-tested tooling.

## Consequences

+ One enforcement and audit point.
+ Tools are reusable across agents.
+ Leverages existing CLIs (gh, git, kubectl, cloud CLIs) with mature feature sets.
+ No protocol dependencies; straightforward to test and debug.
- Each integration needs a CLI wrapper with credential injection.
