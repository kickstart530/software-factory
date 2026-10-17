# ADR-0001: Durable workflow engine orchestrates; the LLM does not

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Agents run for minutes to hours, wait on humans for days, and run on pre-emptible capacity. An LLM "manager" that holds the plan in its context cannot guarantee retries, timeouts or resumption.

## Decision

Use a durable workflow engine (Temporal) for orchestration. The Supervisor's scheduling logic (ready tasks, parallelism, cancellation of downstream tasks on failure) is deterministic code; LLM agents decide *what* to do inside a task. Phase 1 ships the same contract in-process (`factory/orchestrator.py`); Phase 2 moves it into Temporal workflows with each task as an activity that heartbeats its checkpoint ID.

## Consequences

+ Retries, timers, human-wait states and replays come from the engine.
+ Orchestration is testable without a model.
- Temporal is a new operational dependency.
- Re-planning still needs an agent (Planner) invoked by the workflow.
