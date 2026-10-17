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

Run one task against a checkout. The model client reads `ANTHROPIC_API_KEY`
(and optionally `ANTHROPIC_BASE_URL`, for the Model Gateway) from the environment:

```bash
export ANTHROPIC_API_KEY=...
factory run --task task.json --workspace path/to/repo
# if the agent escalates:
factory answer --task task.json --workspace path/to/repo --answer "..."
```

`task.json` is a `Task` (see `schemas/Task.json`). The target repo defines its
gate commands in `.factory/gates.yaml`; see this repo's own file for an example.
Checkpoints, traces and spilled tool output go to `.factory-runs/`.

## GitHub Actions

The repository secret `ANTHROPIC_API_KEY` is mapped into the steps that need it:

- **`ci` / `live-smoke`**: one real API round trip (`pytest -m live`) on pushes to
  `main` and manual runs. Pull requests skip it, so they don't spend and fork PRs
  (which get no secrets) still pass.
- **`factory-run`**: manual workflow that runs `factory run` for a Task JSON file
  in this repo and uploads traces and checkpoints as an artifact.

The key is only read by the harness's model client. Agent shell commands and
verification gates run with a minimal environment that excludes it, and tool
and gate output is scanned for credentials and redacted before the model sees it.

## Layout

| Path | What |
|---|---|
| `factory/harness/` | Agent loop, tools, policy, hooks, gates, budgets, checkpoints, model client |
| `factory/schemas.py` | Contracts: `WorkOrder`, `Task`, `TaskDAG`, `TaskResult`, agent outputs |
| `factory/orchestrator.py` | Supervisor that runs a task DAG |
| `agents/` | Agent definitions and prompts |
| `config/models.yaml` | Model tier routing and pricing |
