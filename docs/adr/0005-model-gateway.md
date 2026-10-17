# ADR-0005: Models are reached through a gateway; model IDs live only in config

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Different roles need different cost/quality trade-offs, providers have outages, and model versions change.

## Decision

The harness calls a `ModelClient` interface. Agents name a *tier* (`frontier`, `mid`, `small`); `config/models.yaml` maps tiers to a model and effort level and holds pricing for the budget ledger. All tiers start on the same model at different effort levels; a tier moves to a different model only after the eval suite shows it holds quality (a cascade across models also forfeits prompt-cache reuse). The Anthropic client streams, uses adaptive thinking, caches the stable prefix (system prompt and sorted tool list), and opts into server-side refusal fallbacks.

## Consequences

+ Model changes are config changes gated by evals.
+ One place for quotas, caching and fallback.
- The gateway is on the critical path and must be highly available.
