# Temporal Integration — Phase 2a

This document describes the Temporal-based Supervisor implementation for durable
TaskDAG orchestration (AD-1).

## Overview

The factory orchestrates work via a **Supervisor** that schedules and monitors tasks
in a TaskDAG. Phase 1 implemented this as an in-process `ThreadPoolExecutor`
(`factory/orchestrator.py`) for local development and testing. Phase 2a moves the
same logic into Temporal workflows for production use.

**Why Temporal?** Durable workflows survive crashes, support long-running human-wait
states (escalations), provide observability, and handle retries/timeouts declaratively.

## Architecture

```
                     ┌─────────────────────────────────┐
                     │   Temporal Server               │
                     │   (Cloud or self-hosted)        │
                     └───────────┬─────────────────────┘
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
         ┌──────▼──────┐                  ┌──────▼──────┐
         │   Client    │                  │   Worker    │
         │  (submits   │                  │  (executes  │
         │  workflows) │                  │  workflows) │
         └─────────────┘                  └──────┬──────┘
                                                 │
                                      ┌──────────┴──────────┐
                                      │                     │
                              ┌───────▼────────┐   ┌───────▼────────┐
                              │  Workflow:     │   │  Activity:     │
                              │  ExecuteTaskDAG│───│  run_task      │
                              │                │   │  (spawns       │
                              │  Schedules     │   │   sandbox)     │
                              │  ready tasks   │   │                │
                              └────────────────┘   └────────────────┘
```

### Components

1. **ExecuteTaskDAGWorkflow**: Durable workflow that schedules tasks, enforces
   dependencies, and handles cancellation. It maintains the same logic as
   `orchestrator.Supervisor.execute()`.

2. **run_task_activity**: Activity that executes one task. In production, this
   spawns an ephemeral Kubernetes pod (gVisor sandbox), clones the repo, runs
   the harness, and streams checkpoints via heartbeats.

3. **Client**: Submits Work Orders as workflows (e.g., from the GitHub App or API).

4. **Worker**: Polls for workflow tasks and executes them. One worker pool per
   deployment; autoscales with load.

## Contract Compatibility

Both the in-process `Supervisor` and the Temporal `ExecuteTaskDAGWorkflow` honor
the same **TaskRunner** contract:

```python
TaskRunner = Callable[[Task], TaskResult]
```

This ensures:
- The harness (`factory/harness/loop.py`) is unchanged
- Local tests use the in-process Supervisor
- Production uses Temporal without touching task execution logic

## Development Setup

### Prerequisites

- Python 3.12+
- Temporal server (local dev or Temporal Cloud)

### Local Temporal Server (Docker)

```bash
# Start Temporal server and UI
docker run --rm -d -p 7233:7233 -p 8233:8233 \
  --name temporal-dev temporalio/auto-setup:latest

# Temporal UI is at http://localhost:8233
```

### Running a Worker

```python
from temporalio.client import Client
from factory.temporal_supervisor import create_temporal_worker, TemporalConfig
from factory.sandbox import run_task_in_sandbox


async def main():
    config = TemporalConfig(host="localhost:7233", namespace="default")
    client = await config.create_client()

    # Production worker: spawns sandbox pods for tasks
    worker = await create_temporal_worker(
        client,
        "factory-tasks",
        runner=run_task_in_sandbox,  # Async runner that spawns Kubernetes pods
    )
    await worker.run()


# Run with: python -m asyncio <module>
```

### Executing a Workflow

```python
from temporalio.client import Client
from factory.temporal_supervisor import execute_dag_via_temporal, TemporalConfig
from factory.schemas import TaskDAG


async def main():
    config = TemporalConfig(host="localhost:7233")
    client = await config.create_client()

    # Your TaskDAG
    dag = TaskDAG(...)

    # Execute and wait for results
    results = await execute_dag_via_temporal(client, dag)
    print(results)
```

## Production Deployment (Phase 2a completion)

When Phase 2a is complete, the production deployment will include:

1. **Temporal Cloud** or self-hosted Temporal cluster (on Kubernetes)
2. **Worker pool**: autoscaling Kubernetes deployment that runs workers
3. **Sandbox spawning**: `run_task_activity` launches gVisor pods via `SandboxSpawner`
4. **GitHub App**: receives webhooks, submits Work Orders as Temporal workflows
5. **Checkpoint streaming**: activities heartbeat checkpoint IDs for observability

### Sandbox Pod Architecture

See [`factory/sandbox/README.md`](../factory/sandbox/README.md) for details on:
- **Credential isolation**: Clean HOME, no host mounts (~/.ssh, ~/.config, ~/.netrc)
- **Network isolation**: NetworkPolicy with deny-all default, optional egress proxy
- **Runtime isolation**: gVisor/Kata runtime class, resource quotas, seccomp
- **Explicit credential injection**: Via Secrets Broker (Phase 2b)

## Testing

Phase 2a includes two levels of testing:

1. **Unit tests** (`tests/test_temporal_supervisor.py`): verify workflow logic,
   dependency ordering, cancellation, without a Temporal server

2. **Integration tests** (marked `@pytest.mark.skip` until Phase 2a completion):
   use Temporal's time-skipping test environment to run full workflows

Run tests:

```bash
pytest tests/test_temporal_supervisor.py -v
```

## What's NOT in Phase 2a First Slice

This PR is the **first slice** of Phase 2a. It delivers:

- ✅ Temporal workflow/activity definitions
- ✅ Same TaskRunner contract as in-process Supervisor
- ✅ Unit tests and structure
- ✅ Dependencies added to `pyproject.toml`
- ✅ **Sandbox pod spawning implementation**
- ✅ **Credential isolation (clean HOME, no host mounts)**
- ✅ **Egress control (NetworkPolicy + proxy config)**
- ✅ **TaskRunner injection via registry (not heartbeats)**

Still to do (remaining Phase 2a work):

- ❌ GitHub App integration
- ❌ Full integration tests with Temporal test environment
- ❌ Production Temporal cluster setup (IaC)
- ❌ Worker deployment (Kubernetes manifests)
- ❌ Checkpoint streaming via activity heartbeats
- ❌ Secrets Broker integration (explicit credential injection)

Phase 2b will add:

- MCP tool gateway
- OPA policy engine
- Human approval service (UI + Slack)

## References

- [Temporal Python SDK docs](https://docs.temporal.io/dev-guide/python)
- [ADR-0001: Durable workflow orchestration](adr/0001-durable-workflow-orchestration.md)
- [Implementation Plan](IMPLEMENTATION_PLAN.md) § Phase 2a
