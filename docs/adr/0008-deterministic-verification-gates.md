# ADR-0008: Deterministic verification before LLM judgement

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Models can claim tests pass when they do not, and LLM review alone misses regressions that tests catch.

## Decision

An agent declares the gates it must pass (build, unit tests, lint, secret scan, ...). Repositories define gate commands in `.factory/gates.yaml`. After `submit_result` the harness runs the gates itself; the task succeeds only if all pass. Failures are fed back to the agent up to `max_gate_attempts`. A required gate the repository does not define fails closed. LLM review (Reviewer, Security Reviewer) runs after gates, on a separate context.

## Consequences

+ Success means something checkable.
+ Gate output gives the agent precise feedback.
- Repositories need gate definitions before onboarding.
