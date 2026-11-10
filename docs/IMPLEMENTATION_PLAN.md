# Software Factory — Implementation Plan

A plan for building a **software factory**: a cloud-hosted system where specialised
AI agents take a work request (feature, bug, migration) from intake to a merged,
deployed, observable change. Humans set intent, approve at risk gates, and own
outcomes. All agents run in the cloud; nothing depends on a developer laptop.

The plan rests on one idea: **the harness matters more than the agent count.**
A small set of agents on a strong harness (sandboxing, verification, budgets,
memory, observability, evals) will beat a large swarm on a weak one. So the
harness is built first and every agent is built on it.

---

## Implementation status

"Built" means the code exists and is tested; a phase is only **Done** once its
exit criterion (§10) is met on real runs. Reviewed November 2026.

| Phase | State | Built | Still missing |
|---|---|---|---|
| 0. Foundations | **Done** (repo side) | ADR-0001..0009, core contracts in `factory/schemas.py` (exported to `schemas/`), CI, Terraform for AWS (EKS, RDS, S3), local kind setup, ops docs. | Cloud environment not yet stood up from the IaC. |
| 1. Harness MVP | **Built; exit criterion not met** | `factory/harness/`: agent loop, tools, policy, hooks, gates (snapshotted at task start), budgets, checkpoints/resume, escalation, kill switch, OTel exporter, model client. Replay runner and 5 golden tasks. | Exit criterion is 30% of a **50**-task set on the **real** model; there are 5 tasks, replayed from recordings. |
| 2a. Temporal + GitHub App pilot | **Built; not run on a cluster** | Temporal workflow and activity (tested against Temporal's test server), `factory.worker`, sandbox pods that run `factory sandbox-entry` and report the TaskResult, agent-harness image, webhook receiver gated to repo collaborators. | No end-to-end run on a real cluster yet. Sandboxes clone public repos only (no clone token from the Secrets Broker) and don't push branches or open PRs. Planner isn't invoked: every Work Order is one implementer task. |
| 2b. Policy & approvals | **Partial** | `RulePolicy`, OPA policy (fails closed), approval service and `factory approve` CLI. Operations that need approval are refused. | The loop doesn't yet pause for an approval and resume on the decision. |
| 3. Hardening | **Partial** | Security Reviewer and Test Engineer agents, adversarial evals, Secrets Broker (no secrets on disk), budget ledger, cost dashboards, kill switch (honored by harness, Supervisor and workflow), API key kept out of agent-readable environments. | Secrets Broker and budget ledger aren't used at runtime yet. |
| 4. Full lifecycle | **Partial** | Spec, Architect, Release, SRE and Docs agent definitions; memory lessons with promotion; canary + rollback orchestration. | These agents aren't scheduled by the pipeline yet. |
| 5. Continuous improvement | **Partial** | Eval gate (`factory evals gate`), A/B routing (sticky per Work Order), golden task generation, per-repo tuning. | The eval gate replays recorded responses, so it can evaluate budget and attempt-limit changes only; prompt and model changes are reported as inconclusive until golden tasks are re-recorded live. |

---|---|---|
| 0. Foundations | **Complete** | ADR-0001..0009 in [`docs/adr/`](adr/); core contracts in `factory/schemas.py`, exported to `schemas/`; CI workflow. Cloud IaC: Terraform modules for AWS (EKS, RDS, S3) in `infra/terraform/`, local kind environment in `infra/local/`. Hello-world sandbox with OpenTelemetry tracing. Ops documentation in [`docs/ops/`](ops/). |
| 1. Harness MVP | **Complete** | `factory/harness/`: agent loop, tool registry and sandbox tools, policy, hooks, gates, budgets, checkpoints/resume, escalation, telemetry (JSONL + OpenTelemetry exporter), model client. `evals/`: golden task set (5 tasks), replay runner, CLI integration. 50+ tests on a scripted model. |
| 2a. Temporal Supervisor + GitHub App pilot | **Complete** | In-process Supervisor (`factory/orchestrator.py`) for local testing. Temporal workflow/activities in `factory/temporal_supervisor.py`. Temporal cluster infrastructure in `infra/temporal/`. Sandbox pod spawner with credential isolation in `factory/sandbox/`. GitHub App webhook receiver (`github_app/`) integrated with Temporal workflow starts. Agent definitions for planner, implementer, reviewer and integrator in `agents/`. End-to-end integration operational; production pipeline ready for pilot repository deployment. |
| 2b. Policy & Approvals | **Complete** | OPA-compatible policy engine (`factory/harness/policy_engine.py`), human approval service (`factory/harness/approvals.py`) with CLI interface (`factory approve` subcommands). Risk-tier computation for path-based and change-based scoring. CLI-first per ADR-0009 (MCP deferred). Integration guide at [`docs/phase2b-integration-guide.md`](phase2b-integration-guide.md). Slack UI for approvals deferred to Phase 4. |
| 3. Hardening | **Complete** | Security Reviewer + Test Engineer agents, injection + adversarial evals suite, Secrets Broker integration, multi-tenancy support + budget ledger, cost dashboards, kill switch implementation, spot instance + pre-emption handling. PRs #15–#20 merged to main. |
| 4. Full lifecycle | **Complete** | Spec, Architect, Release, SRE, and Docs agents with schemas in `factory/agents/` and `factory/schemas.py`; memory lessons with promotion workflow in `factory/memory/`; canary deployment + auto-rollback in `factory/release/`. Delivered in PRs #21 (plan status), #22 (agents + schemas), #24 (memory lessons + promotion), #23 (canary + rollback). Phase status updated in [#26](https://github.com/kickstart530/software-factory/pull/26). |
| 5. Continuous improvement | **Complete** | Eval-gated prompt/model updates ([#27](https://github.com/kickstart530/software-factory/pull/27), `factory evals gate`, docs/EVAL_GATING.md), A/B model routing ([#28](https://github.com/kickstart530/software-factory/pull/28), `config/experiments.yaml`, `factory experiments`), golden task generation ([#29](https://github.com/kickstart530/software-factory/pull/29), `factory golden`, factory/golden_gen/), per-repo tuning ([#30](https://github.com/kickstart530/software-factory/pull/30), `.factory/repo.yaml`, `factory repo-config`). Continuous-improvement loops now operational via CLI. |

---

## 1. Goals and non-goals

**Goals**

1. Turn a structured *Work Order* into a reviewed, tested, merged pull request with no human keystrokes between approval gates.
2. Make every agent run **reproducible, auditable, interruptible and resumable**.
3. Enforce quality with **deterministic gates first** (build, test, lint, SAST), with LLM judgement layered on top, not used in their place.
4. Bound every run by **cost, time and blast radius**.
5. Measure the factory with evals and production metrics, so changes to prompts, models or the harness are tested like code changes.

**Non-goals (for v1)**

- Agents that talk to each other in free-form chat. Coordination goes through typed artifacts and events.
- Fully autonomous production deploys for high-risk changes. A human approves them.
- Building our own model. Models are pluggable through a gateway.

---

## 2. Architecture overview

```
                ┌──────────────────────────── CONTROL PLANE ────────────────────────────┐
 Humans ──►  Intake API / UI / GitHub & Slack webhooks
                │
                ▼
         ┌──────────────┐   Work Order    ┌─────────────────────┐   events   ┌───────────┐
         │ Factory      │ ──────────────► │ Workflow Engine     │ ─────────► │ Event Bus │
         │ Supervisor   │ ◄────────────── │ (durable, Temporal) │ ◄───────── │ (NATS /   │
         └──────────────┘   status/escal. └─────────┬───────────┘            │  Kafka)   │
                │                                   │ schedules tasks         └───────────┘
   Policy Engine (OPA) · Budget Ledger · Audit Log · Secrets Broker · Approval Service
                └───────────────────────────────────┼───────────────────────────────────┘
                                                    ▼
                ┌───────────────────────────── DATA PLANE ──────────────────────────────┐
                │  Agent Runner pool (Kubernetes)                                       │
                │   ┌──────────────── one task = one ephemeral sandbox ───────────────┐ │
                │   │  AGENT HARNESS                                                   │ │
                │   │  loop · context mgr · tool router · hooks · checkpoints · budget │ │
                │   │  workspace: git clone, language toolchains, test runners         │ │
                │   └──────────────────────────────────────────────────────────────────┘ │
                │        │ model calls            │ tool calls          │ artifacts       │
                │        ▼                        ▼                     ▼                 │
                │  Model Gateway           Tool Gateway (CLI)     Artifact Store           │
                │  routing · caching ·     git host · CI · issue  (git + object store +   │
                │  fallback · rate limit   tracker · cloud APIs   Postgres metadata)      │
                │                                                                          │
                │  Memory & Knowledge service (code index, ADRs, run lessons, vector+graph)│
                └──────────────────────────────────────────────────────────────────────────┘
                                   │ OpenTelemetry traces / metrics / logs
                                   ▼
                     Observability + Eval Service (traces, replays, scorecards)
```

### Key architectural decisions

| # | Decision | Rationale |
|---|----------|-----------|
| AD-1 | **Durable workflow engine** (Temporal) orchestrates work, not an LLM | Retries, timeouts, resumability and human-wait states are solved problems. The LLM decides *what*; the engine guarantees *that it happens*. |
| AD-2 | **One task = one ephemeral sandbox** (gVisor / Firecracker microVM on K8s) | Isolation, clean state, and a bounded blast radius. Sandboxes are cattle. |
| AD-3 | **Blackboard coordination** over typed artifacts and events | Agents never depend on each other's prompts. Any agent can be swapped, replayed or run in parallel. |
| AD-4 | **Git is the source of truth for code**; every task works on a branch and ends in a PR | Reuses existing review, CI and audit tooling, and gives humans a familiar interface. |
| AD-5 | **Model Gateway** abstracts providers and tiers | Route by task (small/fast for triage, frontier for architecture and review), with fallback, caching and per-tenant quotas. Model IDs live only in config. |
| AD-6 | **Tool Gateway is CLI-first** | One permission, audit and credential-injection point for every external action. Wraps existing CLIs (gh, git, kubectl) with policy and credential injection. Phase 2b implements CLI-first tooling (ADR-0009); MCP gateway deferred. |
| AD-7 | **Policy-as-code** (OPA/Rego) decides what each agent may do | Least privilege per role and per risk tier, reviewed and versioned like code. |
| AD-8 | **Deterministic verification before LLM judgement** | Tests and type checks don't hallucinate. LLM review handles what they can't catch. |

---

## 3. The Agent Harness (build this first)

The harness is the runtime that wraps every model call. Each agent is a
**configuration of the harness** (system prompt, tool set, policies, budget,
output schema), not a separate codebase. Build on the Claude Agent SDK, or an
equivalent agent loop, and add the factory-specific capabilities below.

### 3.1 Capabilities

| Capability | What it does | Implementation notes |
|---|---|---|
| **Agent loop** | Model ↔ tool turns until done, blocked, or out of budget | Stop conditions: structured `result` emitted, budget exceeded, policy denial, or `escalate` tool called. |
| **Task contract** | Typed input (Work Order slice), acceptance criteria, output JSON schema | Output validated against the schema; one repair turn on failure, then fail the task. |
| **Tool router** | Registers tools (local + gateway CLIs), validates args, applies policy, injects credentials | Credentials are **never** placed in model context. The gateway attaches them server-side. |
| **Sandboxed execution** | Shell, file edit and test runners inside the task sandbox | Read-only root FS, writable workspace, CPU/mem/disk quotas, egress via allowlist proxy. |
| **Context manager** | Keeps the working set relevant and under the window | System prompt + task contract pinned; auto-compaction with summaries; retrieval from the Memory service on demand; large tool outputs truncated to a file with a pointer. |
| **Sub-agent spawning** | Delegates bounded sub-tasks (search, review a file) to fresh contexts | Child gets a narrower tool set and a slice of the parent budget; returns a structured summary only. |
| **Hooks** | `pre_tool`, `post_tool`, `pre_commit`, `on_stop` extension points | Used for policy checks, secret scanning, auto-formatting, and "tests must have run" gates. |
| **Checkpoint / resume** | Persists transcript, workspace diff and budget after each turn | Any crash or pre-emption resumes from the last checkpoint. Temporal activity heartbeats carry the checkpoint ID. |
| **Budgets** | Token, $ cost, wall-clock, turn and tool-call caps per task and per Work Order | Enforced by the harness and the Budget Ledger. A soft limit warns the agent; a hard limit stops it with a partial result. |
| **Verification gates** | Declarative checks that must pass before a task may complete | e.g. `build`, `unit_tests`, `lint`, `typecheck`, `sast`, `diff_size < N`. Run by the harness, not trusted from the agent's claim. |
| **Escalation** | `escalate(reason, question, options)` tool | Pauses the workflow in a human-wait state; the answer is resumed into context. |
| **Untrusted-content handling** | Tool output, web pages, issue bodies and PR comments are tagged untrusted | Wrapped in delimiters, never treated as instructions; high-risk tools denied while tainted content sits in a short window (taint tracking). |
| **Model routing & fallback** | Per-role default tier, automatic fallback on overload/error | Through the Model Gateway; prompt caching on stable prefixes (system prompt, repo map). |
| **Telemetry** | One OpenTelemetry span per turn, per tool call and per model call | Attributes: tokens, cost, latency, model tier, policy decisions, gate results. Full transcripts stored for replay. |
| **Deterministic replay** | Re-run a recorded task against a new prompt, model or harness version | Tool results recorded so a replay can stub them; this is the base of offline evals. |

### 3.2 Agent definition format

Agents are declared, versioned and reviewed like code:

```yaml
# agents/implementer.yaml  (live file - see agents/)
name: implementer
version: 1
description: Implements one task, with tests, on its own branch.
model_tier: frontier          # resolved via config/models.yaml
system_prompt: prompts/implementer.md
tools: [read_file, write_file, edit_file, list_files, search_code, run_command, escalate, submit_result]
budget: {tokens: 4000000, usd: 25, wall_clock_s: 2700, turns: 150, tool_calls: 400}
gates: [build, unit_tests, lint, secret_scan]   # commands come from the repo's .factory/gates.yaml
max_gate_attempts: 3
output_schema: ImplementationResult
```

---

## 4. Agent roster

Start with the **core five** (★), then add the rest once the pipeline is stable.

| Agent | Responsibility | Inputs → Outputs | Tier |
|---|---|---|---|
| **Supervisor** ★ | Owns a Work Order end to end: decomposes, schedules, monitors, re-plans, escalates | Work Order → Task DAG, status | frontier |
| **Spec / Product** | Turns a raw request into a Work Order with acceptance criteria; asks clarifying questions | Issue/ticket → `WorkOrder` | mid |
| **Architect** | Design for non-trivial changes; writes an ADR; defines interfaces | WorkOrder + code index → `DesignDoc`, ADR | frontier |
| **Planner** ★ | Breaks the design into small, independently verifiable tasks with dependencies | DesignDoc → `TaskDAG` | frontier |
| **Implementer** ★ | Writes code and tests for one task on its own branch | Task → branch + `ImplementationResult` | frontier/mid |
| **Test Engineer** | Adds or strengthens tests; mutation and coverage checks | Diff → tests + coverage report | mid |
| **Reviewer** ★ | Correctness, design and maintainability review of the PR; blocking vs optional findings | PR diff + DesignDoc → `ReviewReport` | frontier |
| **Security Reviewer** | Threat-models the diff; triages SAST/SCA output; secrets, authz, injection | PR diff → `SecurityReport` | frontier |
| **Integrator / CI Fixer** ★ | Keeps PRs green: resolves merge conflicts, root-causes CI failures, re-pushes | CI events → fix commits | mid |
| **Release** | Changelog, versioning, staged rollout, canary analysis | Merged PRs → release + rollout | mid |
| **SRE / Ops Triage** | Watches alerts after deploy; correlates with recent changes; proposes a rollback or fix | Alerts + traces → incident note / fix Work Order | mid |
| **Docs** | Updates READMEs, API docs and runbooks from the diff | Merged diff → docs PR | small |

**Separation of duties:** an agent never approves its own output. Reviewer and
Security Reviewer run on a different context (and optionally a different model
tier or provider) from the Implementer.

---

## 5. End-to-end workflow

```
Request ─► Spec ─► [Human: approve Work Order] ─► Architect* ─► Planner ─► Task DAG
                                                                              │
          ┌───────────────── for each ready task (parallel, up to N) ─────────┘
          ▼
   Implementer (sandbox) ─► harness gates (build/test/lint/types/secrets)
          │ pass                                  │ fail ×3 → re-plan or escalate
          ▼
   Open PR ─► CI ─► Reviewer + Security Reviewer (parallel)
          │            │ blocking findings → back to Implementer (bounded loops)
          ▼
   Risk-tier gate: low → auto-merge │ medium → 1 human approval │ high → 2 humans + Architect sign-off
          ▼
   Merge ─► Release (canary) ─► SRE watch window ─► Done / auto-rollback
                                                     * Architect only for medium/high complexity
```

**Risk tiers** are computed by policy from the paths touched (auth, payments,
infra, migrations), the diff size, the dependency changes, and the blast-radius
labels on the repo.

**Bounded loops:** every feedback loop (review→fix, CI→fix) has a max iteration
count. When it runs out, the Supervisor re-plans or escalates. It never spins.

**Phase 2a intake:** the full workflow above requires the Spec agent and approval
UI (Phase 4). Phase 2a starts with **hand-authored Work Orders** submitted via
the Temporal API or a simple CLI, skipping the Spec and approval steps. The pilot
validates orchestration, task execution and PR integration on real work without
building the full intake pipeline first.

**Local dogfood:** the `factory` CLI runs tasks in-process for development and
testing. It is not the production runtime. Cloud agents run via Temporal in
sandboxed Kubernetes pods (Phase 2a); the in-process Supervisor remains as a
local test harness.

**Code index timing:** the Memory service (§7) including the code index is
deferred to Phase 4. If Planner or Architect agents must handle large or
unfamiliar repositories in Phase 2a, a minimal code index (symbol extraction
only, no vector embeddings) may need to be pulled forward and integrated earlier.

---

## 6. Cloud platform

| Concern | Choice (reference; cloud-agnostic) |
|---|---|
| Compute | Kubernetes (EKS/GKE/AKS) with a gVisor or Kata/Firecracker runtime class for agent sandboxes; autoscaled node pool; spot nodes for sandboxes (checkpointing makes pre-emption safe) |
| Orchestration | Temporal (Cloud or self-hosted) |
| Event bus | NATS JetStream (or Kafka/PubSub) |
| State | Postgres (work orders, tasks, budgets, audit), object store (transcripts, checkpoints, artifacts) |
| Memory | pgvector (or a managed vector DB) + code-graph index (tree-sitter / SCIP) |
| Identity | Workload identity (OIDC) per sandbox; short-lived, task-scoped tokens minted by the Secrets Broker (Vault) |
| Network | Default-deny egress; allowlist proxy (package registries, git host, model gateway, tool gateway) |
| Git & CI | GitHub/GitLab via a GitHub App; existing CI (Actions) remains the authority for merge |
| Observability | OpenTelemetry → Grafana/Tempo/Loki or Datadog; LLM trace UI (Langfuse/Phoenix or in-house) |
| IaC | Terraform for cloud resources, Helm/Argo CD for cluster workloads |
| Multi-tenancy | Namespace + tenant ID on every record; per-tenant budgets, policies and model quotas |

---

## 7. Memory and knowledge

Three layers, each with an owner and a retention policy:

1. **Working memory**: the task context, managed by the harness and discarded when the task ends (the transcript is archived).
2. **Project knowledge**: repo map, symbol graph, ADRs, conventions (`CLAUDE.md`/`AGENTS.md`), runbooks. Re-indexed on every merge.
3. **Factory lessons**: distilled, *reviewed* learnings from past runs ("CI in repo X needs `make gen` before tests"). Written by a post-run reflection step, **promoted only after human or eval approval**, so bad lessons don't get reinforced.

Retrieval is a tool (`memory.query`), not a blind context stuffing step, so
the agent decides what to pull in and the trace shows why.

---

## 8. Security and governance

- **Least privilege per role**: e.g. Implementer can push to `factory/*` branches but cannot merge; Release can deploy but cannot edit code.
- **No standing credentials**: tokens are minted per task, scoped to one repo and branch, and expire with the task.
- **Prompt-injection defence in depth**: untrusted-content tagging, tool allowlists, taint-aware policy, egress control, and human gates on irreversible actions.
- **Supply chain**: dependency additions require SCA + license check; lockfile changes are flagged for review; build provenance (SLSA) attached to artifacts.
- **Secrets**: pre-commit and pre-push secret scanning in hooks; secrets redacted from transcripts before storage.
- **Audit**: an append-only log of every model call, tool call, policy decision and approval, linked to the Work Order and PR.
- **Kill switch**: global and per-tenant pause in the Supervisor; in-flight tasks checkpoint and stop.

---

## 9. Evaluation and metrics

**Offline (gate every harness/prompt/model change)**

- Golden suite: 100–300 historical tasks from our own repos with known-good solutions and hidden tests (SWE-bench style).
- Scenario suites per agent: review findings precision/recall on seeded bugs; security agent on seeded vulns; planner DAG quality via rubric.
- Adversarial suite: prompt-injection payloads in issues, comments and dependencies.
- CI job `factory-eval` runs replays and reports deltas. A regression blocks the merge.

**Online (dashboard)**

| Metric | Target to track |
|---|---|
| Work Orders completed without human edits | ↑ |
| First-push CI pass rate | ↑ |
| Review findings accepted by humans | ↑ (precision) |
| Post-merge revert / incident rate | ↓ |
| Cost per merged PR, tokens per task | ↓ |
| Lead time: approval → merge | ↓ |
| Escalation rate and time-to-human-answer | watch |

---

## 10. Phased roadmap

| Phase | Duration | Deliverables | Exit criteria |
|---|---|---|---|
| **0. Foundations** | 2 wks | Repo skeleton, IaC for cluster/Temporal/Postgres/object store, Model Gateway stub, OTel pipeline, ADR-0001..0008 for the decisions above | `terraform apply` brings up an environment; a hello-world sandbox runs with tracing |
| **1. Harness MVP** | 4 wks | Agent loop, tool router, sandbox exec, budgets, checkpoints, hooks, schema-validated outputs, verification gates, transcript store, replay | A single Implementer agent fixes 30% of a 50-task golden set with all gates enforced |
| **2a. Temporal Supervisor + GitHub App** | 3 wks | Move Supervisor into Temporal workflows/activities (keeping in-process version for local tests); GitHub App integration for one pilot repo; hand-authored Work Order intake | Temporal orchestrates TaskDAG execution on pilot repo; Work Orders submitted manually flow to merged PR |
| **2b. CLI Tooling + Policy + Approvals** | 3 wks | CLI-based Tool Gateway (extending `factory` CLI for gh/git/CI operations), OPA policy engine, risk-tier gating, human approval service (UI + Slack) | Low-risk changes auto-merge; medium/high changes gate on human approval via Slack |
| **3. Hardening** | 4 wks | Security Reviewer, Test Engineer, injection defences + adversarial evals, Secrets Broker, multi-tenancy, cost dashboards, kill switch, spot/pre-emption tolerance | Pass a security review; ≥ 50% golden-set solve rate; cost per PR within budget |
| **4. Full lifecycle** | 4–6 wks | Spec, Architect, Release, SRE, Docs agents; memory "lessons" with promotion workflow; canary + auto-rollback | Medium-risk changes shipped end to end with one human approval |
| **5. Continuous improvement** | ongoing | Eval-gated prompt/model updates, A/B model routing, per-repo tuning, self-generated golden tasks from merged human PRs | Metrics in §9 trend the right way each month |

Throughout: weekly review of failed runs (transcripts + traces), with the root
cause filed as a harness, prompt, tool or policy fix.

---

## 11. Repository layout

What exists today, then what each later phase adds.

```
software-factory/
├── docs/
│   ├── IMPLEMENTATION_PLAN.md      # this document
│   └── adr/                        # architecture decision records (0001-0008)
├── factory/
│   ├── schemas.py                  # WorkOrder, Task, TaskDAG, TaskResult, agent outputs
│   ├── agents/spec.py              # AgentSpec + AgentCatalog (loads agents/*.yaml)
│   ├── harness/
│   │   ├── loop.py                 # AgentHarness: the agent loop and its invariants
│   │   ├── tools.py                # tool registry, sandbox tools, submit_result
│   │   ├── policy.py               # RulePolicy (OPA-replaceable)
│   │   ├── hooks.py                # pre/post tool hooks: secret guard, redaction
│   │   ├── gates.py                # verification gates (.factory/gates.yaml)
│   │   ├── budget.py               # budget tracker + pricing
│   │   ├── checkpoint.py           # per-turn checkpoints, resume
│   │   ├── model.py                # ModelClient, Anthropic client, scripted client
│   │   └── telemetry.py            # run events (JSONL; OTel exporter later)
│   ├── orchestrator.py             # in-process Supervisor (Temporal in Phase 2)
│   └── cli.py                      # `factory run | answer | agents validate | schemas export`
├── agents/                         # declarative agent definitions (*.yaml) + prompts/
├── config/models.yaml              # tier -> model/effort routing, pricing
├── schemas/                        # exported JSON schemas (CI checks they are in sync)
├── .factory/gates.yaml             # this repo's own gates (the factory dogfoods itself)
├── tests/
└── .github/workflows/ci.yml
```

Later phases add `gateways/` (model, CLI-based tools), `services/` (memory, approvals,
budget ledger), `sandbox/` (images, runtime class, egress proxy), `evals/`
(golden, scenario and adversarial suites) and `infra/` (Terraform, Helm/Argo CD).

**Stack:** Python 3.12+ for the harness, orchestrator and agents (Anthropic SDK,
Pydantic, Temporal Python SDK from Phase 2); Go or Python for the gateways;
TypeScript only for the approval UI.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agents loop or burn budget | Hard budgets, bounded feedback loops, Supervisor re-plan/escalate |
| Plausible but wrong code merges | Deterministic gates, independent review contexts, risk-tier human approval, canary + rollback |
| Prompt injection via issues, deps or web | Untrusted tagging, taint-aware policy, egress allowlist, no credentials in context |
| Context rot on large repos | Retrieval tools, code-graph index, sub-agents for exploration, compaction |
| Model/provider outage or behaviour drift | Gateway fallback, pinned model versions per agent, eval gate before upgrades |
| Humans become a bottleneck | Risk-tiered gates, batched approvals, clear escalation questions with options |
| Lessons memory reinforces mistakes | Promotion only after review/eval; TTL and provenance on every lesson |

---

## 13. Immediate next steps

Status per phase is in the table at the top. In priority order:

1. **First real end-to-end run.** Deploy the worker and the agent-harness image to the
   kind cluster (`infra/local/`), label a pilot issue `factory:build`, and follow it
   through Temporal to a sandbox TaskResult.
2. **Clone and push credentials.** Mint a task-scoped token with the Secrets Broker for
   the sandbox's clone, and push the task branch and open a PR from the sandbox.
3. **Approval wait in the loop.** Pause on `requires_approval`, file an approval
   request, and resume on the decision (today such operations are refused).
4. **Runtime budget ledger.** Charge every model call to the tenant ledger and stop
   work when a tenant is over budget.
5. **Planner in the pipeline.** Have the webhook run the Planner to produce the
   TaskDAG instead of a single implementer task.
6. **Live golden suite.** Grow the golden set toward 50 tasks recorded against the real
   model, so the Phase 1 exit criterion can be measured and the eval gate can evaluate
   prompt and model changes.

See [`docs/GETTING_STARTED.md`](GETTING_STARTED.md) for setup and deployment guides.
