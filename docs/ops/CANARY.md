# Canary Deployment Operations

This document describes the canary deployment orchestration system for the software factory (Phase 4), including operator runbooks and integration patterns for automated SRE agents.

## Overview

The canary deployment system provides:

- **Staged rollout**: Progressive traffic shifts (e.g., 5% → 25% → 100%)
- **Health monitoring**: Automated health checks at each stage with configurable thresholds
- **Auto-rollback**: Automatic revert to previous version on health check failures
- **Audit trail**: Complete decision log for every deployment action
- **CLI-first**: All operations accessible via `factory canary` commands

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Canary Orchestrator                                        │
│  • Manages stage progression                                │
│  • Runs health checks                                       │
│  • Triggers rollbacks                                       │
│  • Records audit trail                                      │
└────────────┬────────────────────────────────────────────────┘
             │
             ├─► DeployBackend Protocol
             │   • deploy_canary(version, traffic_percent)
             │   • rollback(version)
             │   • get_metric(metric_name)
             │   • get_current_version()
             │
             └─► State Persistence (.factory-runs/canary/)
                 • JSON state per run
                 • Recoverable after crashes
```

## Canary Plan Structure

A **CanaryPlan** defines the deployment strategy:

```json
{
  "id": "standard-web-service",
  "name": "Standard 3-stage web service canary",
  "stages": [
    {
      "name": "stage-1",
      "traffic_percent": 5,
      "duration_seconds": 300,
      "health_check_interval_seconds": 30
    },
    {
      "name": "stage-2",
      "traffic_percent": 25,
      "duration_seconds": 600,
      "health_check_interval_seconds": 30
    },
    {
      "name": "stage-3",
      "traffic_percent": 100,
      "duration_seconds": 600,
      "health_check_interval_seconds": 60
    }
  ],
  "health_checks": [
    {
      "name": "error-rate",
      "metric": "error_rate",
      "threshold": 0.05,
      "operator": "<"
    },
    {
      "name": "latency-p99",
      "metric": "latency_p99",
      "threshold": 1000.0,
      "operator": "<"
    }
  ],
  "auto_rollback_enabled": true,
  "watch_window_seconds": 300
}
```

### Stages

Each stage defines:

- **traffic_percent**: Percentage of production traffic routed to the canary (0-100)
- **duration_seconds**: How long to observe at this stage before advancing
- **health_check_interval_seconds**: How often to evaluate health checks

### Health Checks

Health checks are evaluated continuously during each stage:

- **metric**: Metric name to query from the backend (e.g., `error_rate`, `latency_p99`)
- **threshold**: Maximum acceptable value
- **operator**: Comparison operator (`<`, `<=`, `>`, `>=`, `==`)

If **any** health check fails during a stage, the orchestrator:
1. Records the failure in the audit trail
2. Stops progression
3. Triggers auto-rollback (if `auto_rollback_enabled: true`)

## Canary Run Lifecycle

### 1. Create a Run

Define a **CanaryRun** with the version to deploy:

```json
{
  "id": "run-20261102-001",
  "plan_id": "standard-web-service",
  "version": "v2.5.0",
  "previous_version": "v2.4.1"
}
```

### 2. Start the Canary

```bash
factory canary start \
  --plan plans/web-service.json \
  --run runs/run-20261102-001.json \
  --current-version v2.4.1
```

This:
- Marks the run as `IN_PROGRESS`
- Sets `current_stage` to the first stage
- Deploys the canary at the initial traffic percentage
- Records the start decision in the audit trail

### 3. Monitor and Advance Stages

The orchestrator can be driven:

- **Manually** (operator or CI): Call `factory canary advance` after each stage duration
- **Automated** (SRE agent): Poll `factory canary status` and advance on schedule

```bash
# Check current status
factory canary status run-20261102-001

# Advance to next stage after health checks pass
factory canary advance run-20261102-001 --plan plans/web-service.json
```

Each `advance` call:
1. Runs all health checks
2. Records check results in `stage_executions`
3. If all pass: advances to next stage (or marks `COMPLETED` if final stage)
4. If any fail: triggers auto-rollback (if enabled)

### 4. Completion

After the final stage passes health checks, the run is marked `COMPLETED`. The deployment is considered stable.

### 5. Rollback

Rollback can be triggered:

- **Automatically**: When health checks fail and `auto_rollback_enabled: true`
- **Manually**: Operator or SRE agent calls `factory canary rollback`

```bash
factory canary rollback run-20261102-001 \
  --reason "high error rate detected by external monitoring"
```

Rollback:
1. Reverts traffic to `previous_version` via `backend.rollback()`
2. Marks run as `ROLLED_BACK`
3. Records rollback reason and details in the audit trail

## Operator Runbooks

### Starting a Canary Deployment

**When**: After a PR is merged and CI passes.

**Steps**:

1. **Create a canary plan** (if not already defined for this service):

   ```bash
   cat > plans/myservice.json <<EOF
   {
     "id": "myservice-canary",
     "name": "My Service Canary",
     "stages": [
       {"name": "stage-1", "traffic_percent": 10, "duration_seconds": 300},
       {"name": "stage-2", "traffic_percent": 50, "duration_seconds": 600},
       {"name": "stage-3", "traffic_percent": 100, "duration_seconds": 600}
     ],
     "health_checks": [
       {"name": "error-rate", "metric": "error_rate", "threshold": 0.03, "operator": "<"}
     ],
     "auto_rollback_enabled": true,
     "watch_window_seconds": 300
   }
   EOF
   ```

2. **Create a run definition**:

   ```bash
   cat > runs/run-$(date +%Y%m%d-%H%M).json <<EOF
   {
     "id": "run-$(date +%Y%m%d-%H%M)",
     "plan_id": "myservice-canary",
     "version": "$(git rev-parse HEAD)",
     "previous_version": "v1.2.3"
   }
   EOF
   ```

3. **Start the canary**:

   ```bash
   factory canary start \
     --plan plans/myservice.json \
     --run runs/run-20261102-1430.json \
     --current-version v1.2.3
   ```

4. **Monitor**: Use `factory canary status <run-id>` to track progress.

5. **Advance stages** (or let automation handle it):

   ```bash
   factory canary advance <run-id> --plan plans/myservice.json
   ```

### Emergency Rollback

**When**: High error rate, latency spike, or other production incident.

**Steps**:

1. **Identify the active canary run**:

   ```bash
   factory canary list
   ```

2. **Trigger rollback**:

   ```bash
   factory canary rollback <run-id> --reason "P1 incident: error rate spike"
   ```

3. **Verify rollback completed**:

   ```bash
   factory canary status <run-id>
   ```

   Confirm `status: "rolled_back"` and check `rollback_details`.

4. **Check traffic**: Verify production traffic is back on the previous version via your observability dashboard.

### Listing Active Deployments

```bash
factory canary list
```

Output:

```
ID                       Status          Version      Stage                Started
====================================================================================================
run-20261102-001         in_progress     v2.5.0       stage-2              2026-11-02T14:30:00
run-20261101-003         completed       v2.4.1       stage-3              2026-11-01T09:15:00
run-20261101-002         rolled_back     v2.4.0       stage-1              2026-11-01T08:00:00
```

## SRE Agent Integration

An **SRE agent** (Phase 4) can consume canary status and drive the deployment:

### Status Polling Pattern

```python
from factory.release import CanaryOrchestrator, CanaryPlan, FakeBackend
from pathlib import Path

backend = FakeBackend()  # Replace with real backend
orchestrator = CanaryOrchestrator(backend, state_dir=Path(".factory-runs/canary"))

# Load the plan and current run
plan = CanaryPlan.model_validate_json(Path("plans/myservice.json").read_text())
run = orchestrator.load_run("run-20261102-001")

if run.status == "in_progress":
    # Check if it's time to advance
    # (In production, check elapsed time since stage started)
    result = orchestrator.advance_stage(plan, run)
    
    if result.status == "rolled_back":
        # Alert: canary failed and was rolled back
        notify_oncall(f"Canary {run.id} rolled back: {result.rollback_details}")
    elif result.status == "completed":
        # Success: deployment complete
        notify_team(f"Canary {run.id} completed successfully")
```

### Watch Window Pattern

After a canary completes, the SRE agent should:

1. **Wait for watch window** (`plan.watch_window_seconds`)
2. **Monitor post-completion metrics**
3. **Trigger rollback if late-breaking issues appear**

```python
if run.status == "completed":
    time.sleep(plan.watch_window_seconds)
    
    # Re-check health
    all_passed, results = orchestrator.check_health(plan, run)
    if not all_passed:
        orchestrator.rollback(run, reason="health degraded during watch window")
```

## State Persistence

All canary runs are persisted to `.factory-runs/canary/<run-id>.json`:

```json
{
  "id": "run-20261102-001",
  "plan_id": "standard-web-service",
  "version": "v2.5.0",
  "previous_version": "v2.4.1",
  "status": "in_progress",
  "current_stage": "stage-2",
  "started_at": "2026-11-02T14:30:00+00:00",
  "stage_executions": [
    {
      "stage_name": "stage-1",
      "traffic_percent": 5,
      "started_at": "2026-11-02T14:30:00+00:00",
      "completed_at": "2026-11-02T14:35:00+00:00",
      "health_checks_passed": 10,
      "health_checks_failed": 0,
      "status": "healthy"
    }
  ],
  "decisions": [
    {
      "timestamp": "2026-11-02T14:30:00+00:00",
      "decision": "start",
      "reason": "canary deployment initiated"
    },
    {
      "timestamp": "2026-11-02T14:35:00+00:00",
      "decision": "advance",
      "reason": "advanced from stage-1 to stage-2 (25% traffic)"
    }
  ]
}
```

This state is:

- **Recoverable**: Orchestrator can resume after crash
- **Auditable**: Complete decision trail for compliance
- **Queryable**: SRE agent or CLI can inspect at any time

## Deployment Backend Protocol

The orchestrator expects a backend that implements:

```python
class DeployBackend(Protocol):
    def deploy_canary(self, version: str, traffic_percent: int) -> None:
        """Deploy version as canary with specified traffic split."""
        ...

    def rollback(self, version: str) -> None:
        """Rollback to the specified version."""
        ...

    def get_metric(self, metric_name: str) -> float:
        """Query current metric value."""
        ...

    def get_current_version(self) -> str:
        """Get currently deployed stable version."""
        ...
```

### Example Implementations

**Kubernetes + Istio**:

```python
class IstioBackend:
    def deploy_canary(self, version: str, traffic_percent: int) -> None:
        # Update VirtualService with traffic split
        kubectl(f"patch virtualservice myapp --patch '{traffic_split_patch}'")

    def rollback(self, version: str) -> None:
        # Set 100% traffic to stable version
        kubectl(f"patch virtualservice myapp --patch '{stable_patch}'")

    def get_metric(self, metric_name: str) -> float:
        # Query Prometheus or Datadog
        return prometheus_query(metric_name)
```

**AWS Lambda + API Gateway**:

```python
class LambdaBackend:
    def deploy_canary(self, version: str, traffic_percent: int) -> None:
        # Update Lambda alias with weighted routing
        lambda_client.update_alias(
            FunctionName="myfunction",
            Name="prod",
            RoutingConfig={"AdditionalVersionWeights": {version: traffic_percent / 100}},
        )
```

## Metrics and Observability

The orchestrator emits structured logs and state changes that can be ingested by observability systems:

- **Start/advance/complete/rollback events**: Indexed in audit log
- **Health check results**: Stored per stage execution
- **Decision trail**: Complete reasoning for every action

**Future work** (not Phase 4): OpenTelemetry span per canary run, with:
- Span events for each stage transition
- Attributes for health check pass/fail counts
- Links to deployment trace in K8s/cloud provider

## Testing

All canary logic is tested with a `FakeBackend`:

```bash
pytest tests/test_canary.py -v
```

Tests cover:

- Happy path: 3-stage deployment completes successfully
- Auto-rollback: Health check failure triggers rollback
- Manual rollback: Operator can force rollback at any stage
- State persistence: Runs are recoverable after crash
- Audit trail: All decisions are recorded

## Future Enhancements (Post-Phase 4)

- **Progressive delivery strategies**: Blue/green, feature flags, per-region rollout
- **Machine learning-based anomaly detection**: Auto-detect unusual patterns beyond static thresholds
- **Rollback time windows**: Automatically rollback if not approved within N minutes
- **Multi-service coordination**: Canary across dependent services with coordinated rollback
- **Cost-aware deployment**: Slower rollout during peak traffic to limit blast radius

---

**Questions?** See [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) §10 Phase 4 for context, or open an issue.
