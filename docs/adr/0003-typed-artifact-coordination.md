# ADR-0003: Agents coordinate through typed artifacts, not chat

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Free-form agent-to-agent conversation couples agents to each other's prompts, is hard to replay, and hides where a bad decision came from.

## Decision

Agents read and write typed artifacts (`WorkOrder`, `TaskDAG`, `TaskResult`, `ReviewReport`, ...) defined in `factory/schemas.py` and exported to `schemas/`. Each agent's final output is a `submit_result` call whose input schema is its output contract. Events about those artifacts go on the event bus.

## Consequences

+ Any agent can be swapped, replayed, or run in parallel.
+ Contracts are validated at every hop.
- Schemas need versioning discipline (CI checks exported schemas are in sync).
