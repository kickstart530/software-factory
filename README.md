# software-factory

A cloud-hosted factory of AI agents that takes work from intake to a merged,
deployed change. Every agent is a configuration of one shared **agent harness**:
sandboxed tools, policy checks, verification gates, budgets, checkpoints and
escalation to humans.

- **[Getting Started](docs/GETTING_STARTED.md)**: Setup, what works locally vs what needs K8s/Temporal, CLI usage
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md): architecture, harness, agent roster, roadmap, status
- [Architecture Decision Records](docs/adr/README.md): key technical decisions and rationale
- [Per-Repository Tuning](docs/PER_REPO_TUNING.md): override agent budgets, gates, and model tiers per repository

**Integration & Deployment:**
- [Temporal Integration](docs/TEMPORAL_INTEGRATION.md): durable workflow orchestration with Temporal
- [GitHub App Setup](docs/GITHUB_APP_SETUP.md): webhook receiver for Work Order intake from issues
- [Infrastructure](infra/README.md): Kubernetes deployment for Temporal cluster, workers, and sandboxes
- [Operations Guide](docs/ops/TEMPORAL_OPERATIONS.md): production cluster monitoring and troubleshooting

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
pytest -q                      # harness tests run on a scripted model, no API key needed
factory agents validate        # check agents/*.yaml
factory schemas export         # regenerate schemas/ after editing factory/schemas.py
```

Run one task against a checkout (needs Anthropic API credentials):

```bash
factory run --task task.json --workspace path/to/repo
# if the agent escalates:
factory answer --task task.json --workspace path/to/repo --answer "..."
```

`task.json` is a `Task` (see `schemas/Task.json`). The target repo defines its
gate commands in `.factory/gates.yaml`; see this repo's own file for an example.
Checkpoints, traces and spilled tool output go to `.factory-runs/`.

Repositories can override agent budgets, gates, and model tiers in `.factory/repo.yaml`:

```bash
factory repo-config validate --workspace path/to/repo
factory repo-config show --workspace path/to/repo --agent implementer
```

See [Per-Repository Tuning](docs/PER_REPO_TUNING.md) for configuration options.

## Layout

| Path | What |
|---|---|
| `factory/harness/` | Agent loop, tools, policy, hooks, gates, budgets, checkpoints, model client |
| `factory/schemas.py` | Contracts: `WorkOrder`, `Task`, `TaskDAG`, `TaskResult`, agent outputs |
| `factory/orchestrator.py` | Supervisor that runs a task DAG |
| `agents/` | Agent definitions and prompts |
| `config/models.yaml` | Model tier routing and pricing |
