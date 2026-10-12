# software-factory

A cloud-hosted factory of AI agents that takes work from intake to a merged,
deployed change. Every agent is a configuration of one shared **agent harness**:
sandboxed tools, policy checks, verification gates, budgets, checkpoints and
escalation to humans.

- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md): architecture, harness, agent roster, roadmap, status
- [Architecture Decision Records](docs/adr/README.md)

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

## Layout

| Path | What |
|---|---|
| `factory/harness/` | Agent loop, tools, policy, hooks, gates, budgets, checkpoints, model client |
| `factory/schemas.py` | Contracts: `WorkOrder`, `Task`, `TaskDAG`, `TaskResult`, agent outputs |
| `factory/orchestrator.py` | Supervisor that runs a task DAG |
| `agents/` | Agent definitions and prompts |
| `config/models.yaml` | Model tier routing and pricing |
