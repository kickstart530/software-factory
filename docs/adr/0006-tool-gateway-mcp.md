# ADR-0006: All external actions go through a tool gateway (MCP)

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Agents need to act on git hosts, CI, trackers and cloud APIs. Giving each agent credentials directly spreads secrets into contexts and sandboxes.

## Decision

External tools are exposed through MCP servers behind a tool gateway that authenticates the calling task, applies policy, injects credentials server-side and writes the audit log. Credentials never appear in model context.

## Consequences

+ One enforcement and audit point.
+ Tools are reusable across agents.
- Each integration needs an MCP adapter.
