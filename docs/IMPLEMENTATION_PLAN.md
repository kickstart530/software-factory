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
                │  Model Gateway           Tool Gateway (MCP)     Artifact Store           │
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
| AD-6 | **Tool Gateway speaks MCP** | One permission, audit and credential-injection point for every external action. |
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
| **Tool router** | Registers tools (local + MCP), validates args, applies policy, injects credentials | Credentials are **never** placed in model context. The gateway attaches them server-side. |
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
# agents/implementer.yaml
name: implementer
version: 3
model_tier: frontier          # resolved by the Model Gateway
system_prompt: prompts/implementer.md
tools: [fs.read, fs.edit, shell.exec, git.commit, code_search, memory.query, escalate]
mcp_servers: [git-host, ci]
policy: policies/implementer.rego
budget: { tokens: 2_000_000, usd: 15, wall_clock: 45m, turns: 150 }
gates: [build, unit_tests, lint, typecheck, secret_scan]
output_schema: schemas/implementation_result.json
sub_agents: [code_explorer, test_runner]
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
| **2. Core pipeline** | 4 wks | Supervisor, Planner, Implementer, Reviewer, Integrator; Temporal workflows; GitHub App; Tool Gateway (MCP); policy engine; risk tiers; human approval UI/Slack | Real low-risk Work Orders flow from issue to merged PR on 1–2 pilot repos |
| **3. Hardening** | 4 wks | Security Reviewer, Test Engineer, injection defences + adversarial evals, Secrets Broker, multi-tenancy, cost dashboards, kill switch, spot/pre-emption tolerance | Pass a security review; ≥ 50% golden-set solve rate; cost per PR within budget |
| **4. Full lifecycle** | 4–6 wks | Spec, Architect, Release, SRE, Docs agents; memory "lessons" with promotion workflow; canary + auto-rollback | Medium-risk changes shipped end to end with one human approval |
| **5. Continuous improvement** | ongoing | Eval-gated prompt/model updates, A/B model routing, per-repo tuning, self-generated golden tasks from merged human PRs | Metrics in §9 trend the right way each month |

Throughout: weekly review of failed runs (transcripts + traces), with the root
cause filed as a harness, prompt, tool or policy fix.

---

## 11. Proposed repository layout

```
software-factory/
├── docs/
│   ├── IMPLEMENTATION_PLAN.md      # this document
│   └── adr/                        # architecture decision records
├── harness/                        # agent runtime: loop, context, tools, hooks, budgets, checkpoints
├── agents/                         # declarative agent definitions (*.yaml) + prompts/
├── schemas/                        # JSON schemas: WorkOrder, TaskDAG, results, reports
├── policies/                       # OPA/Rego policies per role and risk tier
├── orchestrator/                   # Temporal workflows & activities, Supervisor
├── gateways/
│   ├── model/                      # routing, fallback, caching, quotas
│   └── tools/                      # MCP servers / adapters (git host, CI, tracker, cloud)
├── services/
│   ├── memory/                     # indexing + retrieval
│   ├── approvals/                  # human-in-the-loop API + Slack/GitHub integration
│   └── budget-ledger/
├── sandbox/                        # base images, runtime class, egress proxy config
├── evals/                          # golden tasks, scenario & adversarial suites, runners
├── infra/                          # Terraform + Helm/Argo CD
└── .github/workflows/              # CI incl. factory-eval
```

**Suggested stack:** Python 3.12 for harness, orchestrator and agents (Claude
Agent SDK, Temporal Python SDK, Pydantic for schemas); Go or Python for the
gateways; TypeScript only for the approval UI.

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

1. Write ADR-0001..0008 capturing AD-1..AD-8 (Phase 0).
2. Define the `WorkOrder`, `Task`, `TaskDAG` and `*Result` JSON schemas. Everything else depends on them.
3. Stand up the sandbox runtime and egress proxy, then prove isolation with a red-team test.
4. Build the harness MVP and the first 50 golden tasks **in parallel**. The eval suite is how we'll know the harness works.
