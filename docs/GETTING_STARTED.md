# Getting Started with Software Factory

This guide will help you understand and start using the Software Factory — a framework that turns work orders into verified, gated, agent-driven software changes.

## What is Software Factory?

Software Factory is a cloud-hosted system where specialized AI agents take a work request (feature, bug fix, migration) from intake through to a merged, deployed change. The system enforces quality through deterministic verification gates (tests, lints, type checks) before any LLM review, bounds every run by cost and time budgets, and requires human approval at risk-appropriate gates.

The core idea: **the harness matters more than the agent count.** Every agent is a configuration of one shared **agent harness** that provides sandboxed tools, policy enforcement, verification gates, budgets, checkpoints, escalation, and observability.

## Current State

Software Factory is under active development. Here's what exists today:

### Phase 0: Foundations (Partial)
- ✅ Core data contracts: `WorkOrder`, `Task`, `TaskDAG`, `TaskResult`
- ✅ Architecture Decision Records (ADR-0001 through 0008)
- ✅ CI workflow
- ⏳ Cloud infrastructure and sandbox pods: **still in progress**

### Phase 1: Harness MVP (Present)
- ✅ Agent loop with model ↔ tool turns
- ✅ Tool registry with sandboxed file and shell tools
- ✅ Policy engine (rule-based; OPA integration planned)
- ✅ Verification gates with configurable commands
- ✅ Budget tracking (tokens, USD, wall-clock, turns, tool calls)
- ✅ Checkpoint/resume support
- ✅ Escalation to humans
- ✅ Telemetry (JSONL traces; OpenTelemetry exporter planned)
- ✅ 50+ harness tests on a scripted model (no API key needed)

### Phase 2a: Core Pipeline (Complete)
- ✅ In-process Supervisor (`factory/orchestrator.py`)
- ✅ Agent definitions: `planner`, `implementer`, `reviewer`, `integrator`
- ✅ **Temporal orchestration**: workflow and activity definitions (see [TEMPORAL_INTEGRATION.md](TEMPORAL_INTEGRATION.md))
- ✅ **GitHub App integration**: webhook receiver and API client (see [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md))
- ✅ **Temporal cluster infrastructure**: deployment manifests and ops guides (see [infra/temporal/README.md](../infra/temporal/README.md), [docs/ops/TEMPORAL_OPERATIONS.md](ops/TEMPORAL_OPERATIONS.md))
- ✅ **Sandbox pods**: Kubernetes spawner with credential isolation (see [factory/sandbox/README.md](../factory/sandbox/README.md))

### Phase 2b: Policy and Approvals (Complete)
- ✅ **Policy engine**: OPA-based policy evaluation for tool calls and risk assessment
- ✅ **Human approval workflow**: CLI-based approval service with Slack notifications
- ✅ **Risk tier computation**: Path-based and change-based risk scoring

**Architecture approach:** CLI-first for external tools and near-term development. MCP was evaluated but deferred in favor of CLI-based tool integration (see [ADR-0006](adr/0006-tool-gateway-cli-first.md)).

## Setup

### Prerequisites

**For local CLI development:**
- Python 3.12 or later
- Git
- (Optional) Anthropic API credentials to run tasks against real models

**For production deployment (not needed for local development):**
- Kubernetes cluster with gVisor or Kata Containers support
- Temporal server (Cloud or self-hosted)
- See deployment guides: [`infra/temporal/README.md`](../infra/temporal/README.md), [`factory/sandbox/README.md`](../factory/sandbox/README.md), [`infra/workers/README.md`](../infra/workers/README.md)

### Installation

1. Clone the repository:
```bash
git clone <your-repo-url>
cd software-factory
```

2. Create a virtual environment and install the package with development dependencies:
```bash
# Using uv (recommended)
uv venv && uv pip install -e ".[dev]"

# Or using standard pip
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

3. Verify installation by running tests:
```bash
pytest -q
```

The tests run on a scripted model client, so no API credentials are needed.

### Configuration

If you plan to run tasks with real LLM models, set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-...
```

Model routing is configured in `config/models.yaml`, which maps model tiers (`frontier`, `mid`, `small`) to specific models and pricing.

## CLI Commands

The `factory` CLI provides the following commands:

### Schema Management

Export JSON schemas for the core data contracts:

```bash
factory schemas export [--out schemas/]
```

This regenerates JSON schema files from the Pydantic models in `factory/schemas.py`. Run this after editing schemas.

### Agent Validation

Validate all agent definitions:

```bash
factory agents validate [--agents agents/]
```

This checks that agent YAML files are well-formed and reference valid prompts, tools, and output schemas.

### Running Tasks

Run a single task against a local repository checkout:

```bash
factory run --task task.json --workspace /path/to/repo [--state-dir .factory-runs] [--risk-tier low]
```

- `--task`: Path to a JSON file containing a `Task` (see `schemas/Task.json`)
- `--workspace`: Path to the repository checkout where the agent will work
- `--state-dir`: Directory for checkpoints, traces, and artifacts (default: `.factory-runs`)
- `--risk-tier`: Risk level (`low`, `medium`, `high`) affecting policy enforcement

The command outputs a `TaskResult` JSON to stdout and exits with code 0 on success.

### Answering Escalations

If a task escalates (calls the `escalate` tool), resume it with an answer:

```bash
factory answer --task task.json --workspace /path/to/repo --answer "Your answer here"
```

This resumes from the last checkpoint and injects your answer into the agent's context.

### Additional Options

All commands support:
- `--agents`: Path to agent definitions directory (default: `agents/`)
- `--models`: Path to model routing config (default: `config/models.yaml`)

## Submitting Work

### Work Orders

A **Work Order** is the unit of approved intent: what to build and how we'll know it's done. It includes:

- **Title and description**: What needs to be built
- **Repository reference**: Which repo and base branch
- **Acceptance criteria**: Specific, verifiable conditions for completion
- **Risk tier**: `low`, `medium`, or `high` (affects approval gates)
- **Budget**: Caps on tokens, cost, time, turns, and tool calls
- **Requested by**: Who requested it

See `schemas/WorkOrder.json` for the full schema.

### TaskDAG

A **TaskDAG** breaks a Work Order into independently verifiable tasks with dependencies. Each task:

- References one agent (`planner`, `implementer`, `reviewer`, `integrator`)
- Has its own acceptance criteria and optional budget override
- Declares dependencies on other tasks via task IDs
- Tracks its status: `pending`, `running`, `succeeded`, `failed`, `escalated`, `cancelled`

The Supervisor (orchestrator) schedules ready tasks in parallel and enforces the dependency order.

### Current Intake Process

**Today (Phase 1):** You manually construct a `Task` JSON file and run it with `factory run`. The task contract is:

```json
{
  "id": "task-001",
  "work_order_id": "wo-001",
  "agent": "implementer",
  "title": "Add user login endpoint",
  "instructions": "Implement POST /auth/login with email/password...",
  "acceptance_criteria": [
    {
      "id": "ac-1",
      "description": "Tests pass",
      "verification": "gate",
      "gate": "unit_tests"
    }
  ],
  "depends_on": [],
  "status": "pending"
}
```

**Production deployment:** Automated Work Order intake via:
- **GitHub issues**: Webhook receiver implemented (see [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md)); requires deployed Temporal cluster (see [`infra/temporal/README.md`](../infra/temporal/README.md))
- **Slack notifications**: Human approval workflow includes Slack integration (Phase 2b)
- **Web UI or API**: Planned for later phases

Once deployed, the Supervisor automatically decomposes Work Orders into a TaskDAG using the `planner` agent and schedules execution across the worker pool.

## Phases and Gates

### Verification Gates

Every repository defines its verification commands in `.factory/gates.yaml`. Example:

```yaml
gates:
  build:
    command: "python -m compileall -q factory"
  unit_tests:
    command: "pytest -q"
    timeout_s: 900
  lint:
    command: "ruff check . && ruff format --check ."
```

Gates are:
- **Deterministic**: They produce the same result for the same code
- **Enforced by the harness**: Not trusted from the agent's claim
- **Required before task completion**: An agent's gates (defined in its YAML) must pass

An agent can attempt a gate multiple times (up to `max_gate_attempts`), but if all attempts fail, the task fails or escalates.

### Budget Enforcement

Every agent has a default budget, and each task can override it:

```yaml
budget:
  tokens: 4000000      # Total tokens (input + output + cache)
  usd: 25.0            # Maximum cost in USD
  wall_clock_s: 2700   # 45 minutes
  turns: 150           # Model-tool interaction rounds
  tool_calls: 400      # Total tool invocations
```

When the budget reaches 80% (the soft limit), the agent is warned. At 100%, the task stops with a partial result.

### Escalation

Agents can call the `escalate` tool to pause and ask a human:

```python
escalate(
    reason="The test suite requires a database fixture I cannot create",
    question="Should I mock the database or skip the integration tests?",
    options=["Mock it", "Skip them", "Ask for help setting up the fixture"],
)
```

The harness checkpoints the state and waits. A human answers via `factory answer`, and the task resumes.

### Risk Tiers and Approval Gates

Risk tiers determine approval requirements:

- **Low risk**: Auto-merge after gates + review pass
- **Medium risk**: One human approval required
- **High risk**: Two human approvals + architect sign-off

Risk is computed from the paths touched (auth, payments, infra), diff size, and dependency changes. The policy engine uses OPA rules for evaluation, with Slack-based approval workflows for medium/high-risk changes.

## How the Pieces Fit

### Architecture Overview

```
┌─────────────────────────────────────────┐
│          Work Order (intent)            │
└─────────────────┬───────────────────────┘
                  ▼
         ┌────────────────┐
         │   Supervisor   │ (orchestrates)
         └────────┬───────┘
                  ▼
         ┌────────────────┐
         │     Planner     │ (decomposes)
         └────────┬───────┘
                  ▼
           TaskDAG (tasks + dependencies)
                  │
      ┌───────────┼───────────┐
      ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│Implement │ │ Reviewer │ │Integrator│
└────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │
     └────────────┴────────────┘
                  ▼
            Merged Pull Request
```

### Typed Artifacts

Agents coordinate through **typed artifacts**, not free-form chat:

- **Planner** produces a `TaskDAG`
- **Implementer** produces an `ImplementationResult` (summary, files changed, tests added, criteria met)
- **Reviewer** produces a `ReviewReport` (verdict, findings with severity and location)
- **Integrator** produces an `IntegrationResult` (how CI was fixed)

Each agent's output is validated against its declared `output_schema`.

### Agent Harness

The harness (`factory/harness/`) provides:

- **Loop**: Runs model ↔ tool turns until a result, budget exhaustion, or escalation
- **Tools**: Sandboxed file operations (`read_file`, `write_file`, `edit_file`, `search_code`, `run_command`)
- **Policy**: Rule-based checks on tool calls (least privilege per agent role)
- **Hooks**: Extension points for pre/post-tool actions (secret scanning, auto-formatting)
- **Checkpoints**: Save state after every turn for crash recovery and resume
- **Telemetry**: Record every model call, tool call, and gate result

Every agent is a configuration of this single harness implementation.

### Supervisor

The Supervisor owns a Work Order end-to-end:

1. Decomposes it into a TaskDAG (via the `planner` agent)
2. Schedules ready tasks in parallel (respecting dependencies)
3. Monitors progress and re-plans on failures
4. Escalates to humans when needed

**Available today:**
- In-process implementation: `factory/orchestrator.py` (runs locally via CLI)
- Temporal workflow definitions: `factory/temporal_supervisor.py` (see [TEMPORAL_INTEGRATION.md](TEMPORAL_INTEGRATION.md))

**For production deployment:**
- Temporal cluster and worker pool (see [`infra/temporal/README.md`](../infra/temporal/README.md), [`infra/workers/README.md`](../infra/workers/README.md))
- Sandbox pod spawning for isolated task execution (see [`factory/sandbox/README.md`](../factory/sandbox/README.md))

### Sandboxes

**Local development:** The harness tools execute in the same process as the CLI. Suitable for development and testing on your local machine.

**Production deployment:** Each task runs in an ephemeral Kubernetes pod with full isolation:
- Clean HOME directory (no host credentials accessible)
- gVisor runtime for user-space kernel isolation
- Network policies with egress control (deny-all default)
- Non-root execution with dropped capabilities
- CPU, memory, and disk quotas
- Automatic cleanup after task completion

This provides blast-radius isolation: a task cannot affect other tasks, access host credentials, or make unauthorized network connections. See [`factory/sandbox/README.md`](../factory/sandbox/README.md) for the full security model and deployment guide.

## Further Reading

### Core Documentation
- **[Implementation Plan](IMPLEMENTATION_PLAN.md)**: Full architecture, roadmap, agent roster, cloud platform, risks
- **[Architecture Decision Records](adr/README.md)**: Why we chose durable workflows, ephemeral sandboxes, typed artifacts, git source-of-truth, CLI-first tools, policy-as-code, and deterministic gates

### Integration Guides
- **[Temporal Integration](TEMPORAL_INTEGRATION.md)**: How Temporal workflows orchestrate the task DAG, checkpoints, and human approvals
- **[GitHub App Setup](GITHUB_APP_SETUP.md)**: Installing and configuring the webhook receiver for Work Order intake

### Operations and Deployment
- **[Temporal Operations Guide](ops/TEMPORAL_OPERATIONS.md)**: Production cluster deployment and monitoring
- **[Temporal Infrastructure](../infra/temporal/README.md)**: Kubernetes manifests and configuration for Temporal cluster
- **[Worker Deployment](../infra/workers/README.md)**: Temporal worker pool deployment
- **[Sandbox Deployment Guide](../factory/sandbox/README.md)**: Kubernetes pod spawning with credential isolation and security model

## Development Workflow

1. **Edit schemas:** Modify `factory/schemas.py`, then run `factory schemas export` to regenerate JSON schemas
2. **Add/edit agents:** Update YAML in `agents/`, validate with `factory agents validate`
3. **Change the harness:** Edit `factory/harness/*.py`, write tests in `tests/`, run `pytest`
4. **Run against a real repo:** Create a `Task` JSON, point `--workspace` at a checkout, run `factory run`
5. **Check traces:** Look in `.factory-runs/traces/` for the JSONL transcript

## Getting Help

- Read the [Implementation Plan](IMPLEMENTATION_PLAN.md) for the big picture
- Check the [ADRs](adr/README.md) for architectural decisions and rationale
- Inspect `schemas/*.json` for the full data contracts
- Look at `agents/*.yaml` for example agent configurations
- Review `.factory/gates.yaml` in this repo to see how gates are defined

## What Works Today vs What Needs Infrastructure

### Works Locally (CLI-first development)
- ✅ Running single tasks via the CLI (`factory run`)
- ✅ Harness loop with tools, gates, budgets, checkpoints
- ✅ Agent definitions with declarative configuration
- ✅ Deterministic verification gates
- ✅ Escalation and human-in-the-loop (`factory answer`)
- ✅ Schema validation and export
- ✅ GitHub App webhook receiver (can run with ngrok for local testing)

### Requires Kubernetes + Temporal (production deployment)
- ✅ **Temporal cluster**: Production-grade workflow orchestration with persistence
  - Kubernetes manifests and Kustomize overlays available in [`infra/temporal/`](../infra/temporal/README.md)
  - Operations guide: [`docs/ops/TEMPORAL_OPERATIONS.md`](ops/TEMPORAL_OPERATIONS.md)
- ✅ **Sandbox pods**: Isolated task execution with credential separation
  - `SandboxSpawner`, Kubernetes NetworkPolicy, gVisor runtime
  - Security model and deployment: [`factory/sandbox/README.md`](../factory/sandbox/README.md)
- ✅ **Worker pool**: Autoscaling Temporal workers that spawn sandbox pods
  - Deployment manifests: [`infra/workers/README.md`](../infra/workers/README.md)
- ✅ **Policy engine**: OPA-based policy evaluation and human approval workflows
- 🚧 **End-to-end integration**: Work Order → TaskDAG → parallel execution → merged PR (integration in progress)

### Development Workflow
The factory is **CLI-first**: all core functionality works locally without Kubernetes or Temporal. The in-process Supervisor (`factory/orchestrator.py`) provides the same TaskDAG execution logic that will run in Temporal workflows. This enables rapid iteration and testing without infrastructure dependencies.

**Production deployment** is now possible: all Phase 2a infrastructure components (Temporal cluster, sandbox pods, worker pool) and Phase 2b policy/approval components have deployment guides and manifests. End-to-end integration of the full pipeline is ongoing.

---

**Questions or issues?** Check the existing ADRs and Implementation Plan, or open an issue in the repository.
