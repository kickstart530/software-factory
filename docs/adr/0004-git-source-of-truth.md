# ADR-0004: Git is the source of truth for code

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Agent changes need review, CI, audit and rollback, which teams already do through git and pull requests.

## Decision

Every implementation task works on its own branch and ends in a pull request. Existing CI stays the merge authority. Agents never push to the default branch or rewrite shared history (enforced by policy).

## Consequences

+ Reuses existing review, CI and audit tooling; humans review in a familiar place.
- Throughput is bounded by CI capacity and merge conflicts (handled by the Integrator agent).
