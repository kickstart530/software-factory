# ADR-0007: Policy-as-code decides what each agent may do

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Permissions differ by agent role and by the risk tier of the Work Order, and must be reviewable.

## Decision

Every tool call passes a `Policy.check` before running. The MVP implements `RulePolicy` (denied commands such as force-push and pipe-to-shell; protected paths that need a high-risk Work Order; taint rules that block sensitive tools while untrusted content is in recent context). The interface is small so an OPA/Rego policy can replace it. Pre-tool hooks add checks such as refusing to write secrets.

## Consequences

+ Least privilege per role and risk tier, versioned and tested like code.
- Rules need tuning to avoid blocking legitimate work; denials are reported to the agent with a reason so it can adapt.
