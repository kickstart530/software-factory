# Software Factory Agents

This document describes the agent roster for the software factory. Each agent is
a specialized role with a clear responsibility, inputs, outputs, and tool set.

## Agent Roster

### Core Pipeline Agents (Phase 1-3)

#### Planner
**Responsibility:** Breaks an approved Work Order into small, independently verifiable tasks with dependencies.

- **Inputs:** Work Order (and optionally DesignDoc from Architect)
- **Outputs:** `PlanResult` containing a `TaskDAG`
- **Model tier:** frontier
- **Tools:** read_file, list_files, search_code, escalate, submit_result

The Planner reads the repository to understand scope and conventions, then decomposes
the work into parallel-executable tasks. Each task should be completable in under
an hour with a reviewable diff.

#### Implementer
**Responsibility:** Implements one task with tests on its own branch.

- **Inputs:** `Task`
- **Outputs:** `ImplementationResult` (summary, files changed, tests added, criteria met)
- **Model tier:** frontier or mid
- **Tools:** read_file, write_file, edit_file, list_files, search_code, run_command, escalate, submit_result
- **Gates:** build, unit_tests, lint, secret_scan

The Implementer reads before writing, makes minimal changes, adds tests, runs gates
locally, and commits to the task branch. Never pushes to the default branch.

#### Reviewer
**Responsibility:** Reviews a change for correctness, design, and maintainability.

- **Inputs:** PR diff + acceptance criteria
- **Outputs:** `ReviewReport` (verdict, findings categorized by severity)
- **Model tier:** frontier
- **Tools:** read_file, list_files, search_code, run_command, escalate, submit_result

The Reviewer inspects the diff and surrounding code, reports findings with concrete
failure scenarios, and approves only when there are no blocking findings. Never
reviews its own work (separation of duties).

#### Integrator
**Responsibility:** Keeps PRs green by resolving conflicts and fixing CI failures.

- **Inputs:** PR with conflicts or CI failure events
- **Outputs:** `IntegrationResult` (summary, root cause, fix commits)
- **Model tier:** mid
- **Tools:** read_file, write_file, edit_file, list_files, search_code, run_command, escalate, submit_result
- **Gates:** build, unit_tests, lint, secret_scan

The Integrator merges (never rebases) the base branch to resolve conflicts, or
reproduces and fixes CI failures. Escalates if the failure is not caused by this PR.

---

### Phase 4: Full Lifecycle Agents

#### Spec (Product)
**Responsibility:** Turns a raw request into a Work Order with clear acceptance criteria.

- **Inputs:** Feature request, bug report, or change proposal (issue/ticket)
- **Outputs:** `SpecResult` containing a refined `WorkOrder`
- **Model tier:** mid
- **Tools:** read_file, list_files, search_code, escalate, submit_result

The Spec agent grounds the request in the actual codebase, defines testable acceptance
criteria, identifies the risk tier, and escalates if the request is ambiguous.

#### Architect
**Responsibility:** Designs non-trivial changes and writes ADRs defining interfaces and trade-offs.

- **Inputs:** Work Order + code index
- **Outputs:** `DesignDoc` (summary, scope, interfaces, data flows, trade-offs, ADR path, risks)
- **Model tier:** frontier
- **Tools:** read_file, list_files, search_code, escalate, submit_result

The Architect runs for medium/high complexity Work Orders. It defines interfaces,
data flows, and trade-offs so implementers have a clear path. May draft an ADR
following the repo's template.

#### Release
**Responsibility:** Generates changelog, updates version, and prepares staged rollout plan.

- **Inputs:** Merged PRs since last release
- **Outputs:** `ReleaseResult` (version, changelog entries, rollout stages, risk tier)
- **Model tier:** mid
- **Tools:** read_file, list_files, search_code, run_command, submit_result

The Release agent determines version bumps using semantic versioning, generates
changelog entries, and defines rollout strategy (immediate, staged, or gated).
The rollout plan is declarative; downstream systems execute it.

#### SRE (Ops Triage)
**Responsibility:** Triages production alerts, correlates with recent changes, proposes rollback or fix.

- **Inputs:** Alerts + traces + recent changes
- **Outputs:** `IncidentNote` (severity, root cause hypothesis, correlated changes, recommended action)
- **Model tier:** mid
- **Tools:** read_file, list_files, search_code, escalate, submit_result

The SRE agent correlates incidents with recent deploys, identifies likely root cause,
and recommends mitigation: rollback with command, hotfix Work Order draft, or escalation.
Does not execute rollbacks itself (requires human approval).

#### Docs
**Responsibility:** Updates READMEs, API docs, and runbooks from merged changes.

- **Inputs:** Merged diff
- **Outputs:** `DocsResult` (summary, files/sections updated/added/removed)
- **Model tier:** small
- **Tools:** read_file, write_file, edit_file, list_files, search_code, run_command, submit_result
- **Gates:** lint

The Docs agent reads merged diffs, finds relevant docs, and updates them to match
the new reality. Keeps voice and style consistent, documents what was built (not
what should be built), and runs doc linters.

---

## Agent Invariants

All agents share these invariants (enforced by the harness):

1. **Declarative:** Each agent is a YAML configuration + system prompt + output schema.
   No free-form agent chat.
2. **Bounded:** Every agent has hard caps on tokens, USD cost, wall clock time, turns,
   and tool calls. The harness enforces these.
3. **Sandboxed:** Each agent runs in its own ephemeral sandbox (one task = one sandbox).
4. **Gated:** Agents that produce code changes run verification gates (build, test, lint,
   secret scan) before submission.
5. **Interruptible & resumable:** Checkpoints after every turn. Tasks can be paused,
   resumed, or replayed.
6. **Separation of duties:** An agent never approves its own output. Review and security
   review run in different contexts.

---

## Adding a New Agent

1. Create `agents/<name>.yaml` with name, version, description, model_tier, system_prompt
   path, tools, budget, gates, output_schema.
2. Create `agents/prompts/<name>.md` with the agent's system prompt.
3. Add the output schema to `factory/schemas.py` as a `Contract` subclass and register
   it in `OUTPUT_SCHEMAS`.
4. Add the agent name to the test in `tests/test_agents.py`.
5. Export schemas with `factory schemas export` if CI requires sync.

See existing agents for patterns. All agents must have `submit_result` in their tool set.
