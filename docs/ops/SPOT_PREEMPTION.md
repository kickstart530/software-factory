# Spot Instance and Pre-emption Handling

The factory is designed to run safely on spot/preemptible instances, using checkpoints to
resume gracefully after interruptions. This enables significant cost savings (60-90%) for
agent workloads.

## Overview

**Spot instances** (AWS EC2 Spot, GCP Preemptible VMs, Azure Spot VMs) are spare compute
capacity available at steep discounts. The cloud provider can reclaim them with 30 seconds
to 2 minutes of notice.

The factory handles pre-emption through:

1. **Per-turn checkpointing**: Every agent turn is saved to durable storage
2. **Graceful shutdown**: Pre-emption signals trigger checkpoint-and-exit
3. **Automatic resume**: The orchestrator reschedules the task with the checkpoint
4. **Idempotent tools**: Tools are designed to be safely retried

## Checkpoint Architecture

From [`factory/harness/checkpoint.py`](../../factory/harness/checkpoint.py):

```python
class RunState(BaseModel):
    """Everything needed to continue a run exactly where it stopped."""
    
    task_id: str
    work_order_id: str | None
    agent: str
    agent_version: int
    messages: list[dict[str, Any]]
    usage: Usage
    gate_attempts: int
    # ... other state
```

Checkpoints include:
- Full conversation history (messages)
- Budget/usage state
- Gate attempt counts
- Escalation state (if waiting on human)
- Tool results from the interrupted turn

## Per-Turn Checkpointing

The harness saves a checkpoint after each turn:

```python
from factory.harness.checkpoint import FileCheckpointStore

checkpoint_store = FileCheckpointStore(Path(".factory-runs/checkpoints"))

# After each model response + tool results
checkpoint_id = checkpoint_store.save(run_state)

# The checkpoint_id goes into Temporal activity heartbeats
activity.heartbeat({"checkpoint_id": checkpoint_id})
```

## Pre-emption Signal Handling

On Kubernetes with spot nodes, pre-emption is signaled via:

1. **Pod eviction notice**: API server sends TERM signal
2. **Spot termination notice**: Cloud metadata endpoint (30-120 seconds notice)
3. **Graceful shutdown hook**: Temporal worker drains and checkpoints in-flight activities

Example Temporal activity:

```python
@activity.defn
async def execute_task(task: Task) -> TaskResult:
    harness = create_harness(task)
    
    try:
        # Run the task with periodic heartbeats
        result = await harness.run_async(task)
        return result
    
    except asyncio.CancelledError:
        # Temporal sends cancellation on graceful shutdown
        # Save final checkpoint
        checkpoint_id = harness.checkpoint()
        
        # Return partial result with checkpoint
        return TaskResult(
            task_id=task.id,
            status=TaskStatus.PENDING,
            summary="Checkpointed due to worker shutdown",
            checkpoint_id=checkpoint_id,
        )
```

## Resume from Checkpoint

When a task is rescheduled after pre-emption:

```python
from factory.harness.checkpoint import FileCheckpointStore
from factory.harness.loop import AgentHarness

checkpoint_store = FileCheckpointStore(Path(".factory-runs/checkpoints"))

# Load checkpoint if it exists
run_state = checkpoint_store.load(
    task_id=task.id,
    work_order_id=task.work_order_id
)

if run_state:
    # Resume from checkpoint
    harness = AgentHarness.from_checkpoint(run_state)
    result = harness.resume(task)
else:
    # Fresh start
    harness = AgentHarness(spec, system_prompt, deps, ctx)
    result = harness.run(task)
```

## Temporal Workflow Integration

The Temporal supervisor handles retries automatically:

```python
@workflow.defn
class TaskExecutionWorkflow:
    @workflow.run
    async def run(self, task: Task) -> TaskResult:
        # Configure retry policy for spot pre-emption
        result = await workflow.execute_activity(
            execute_task,
            task,
            start_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(
                # Retry immediately for infra failures (pre-emption)
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=30),
                # Non-retryable: budget exceeded, policy denied
                non_retryable_error_types=["BudgetExceededError", "PolicyDeniedError"],
            ),
        )
        
        return result
```

## Checkpoint Storage

For production deployments, use durable storage:

### Object Storage (S3, GCS, Azure Blob)

Replace `FileCheckpointStore` with an object store backend:

```python
class S3CheckpointStore:
    def __init__(self, bucket: str, s3_client):
        self.bucket = bucket
        self.s3 = s3_client
    
    def save(self, state: RunState) -> str:
        checkpoint_id = f"{time.time_ns()}"
        key = f"checkpoints/{state.work_order_id}/{state.task_id}/{checkpoint_id}.json"
        
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=state.model_dump_json(),
        )
        
        # Update LATEST pointer
        self.s3.put_object(
            Bucket=self.bucket,
            Key=f"checkpoints/{state.work_order_id}/{state.task_id}/LATEST",
            Body=checkpoint_id,
        )
        
        return checkpoint_id
    
    def load(self, task_id: str, work_order_id: str | None = None) -> RunState | None:
        try:
            # Get latest checkpoint ID
            latest = self.s3.get_object(
                Bucket=self.bucket,
                Key=f"checkpoints/{work_order_id}/{task_id}/LATEST"
            )["Body"].read().decode()
            
            # Load checkpoint
            obj = self.s3.get_object(
                Bucket=self.bucket,
                Key=f"checkpoints/{work_order_id}/{task_id}/{latest}.json"
            )
            
            return RunState.model_validate_json(obj["Body"].read())
        except self.s3.exceptions.NoSuchKey:
            return None
```

### Database (Postgres)

For transactional consistency with other factory state:

```python
class PostgresCheckpointStore:
    def save(self, state: RunState) -> str:
        checkpoint_id = f"{time.time_ns()}"
        
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO checkpoints (id, work_order_id, task_id, state, created_at)
                VALUES (%(id)s, %(work_order_id)s, %(task_id)s, %(state)s, NOW())
            """, {
                "id": checkpoint_id,
                "work_order_id": state.work_order_id,
                "task_id": state.task_id,
                "state": state.model_dump_json(),
            })
            
            cur.execute("""
                UPDATE checkpoints_latest
                SET checkpoint_id = %(id)s, updated_at = NOW()
                WHERE work_order_id = %(work_order_id)s AND task_id = %(task_id)s
            """, {
                "id": checkpoint_id,
                "work_order_id": state.work_order_id,
                "task_id": state.task_id,
            })
        
        self.conn.commit()
        return checkpoint_id
```

## Kubernetes Configuration

Enable spot instances with pod disruption budgets:

```yaml
# taskexecutor-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: factory-task-executor
spec:
  replicas: 10
  template:
    spec:
      # Schedule on spot nodes
      nodeSelector:
        workload: spot
      tolerations:
      - key: "spot"
        operator: "Exists"
      
      # Graceful shutdown settings
      terminationGracePeriodSeconds: 120  # 2 minutes for checkpoint
      
      containers:
      - name: temporal-worker
        image: factory-worker:latest
        lifecycle:
          preStop:
            exec:
              # Drain worker and checkpoint in-flight activities
              command: ["/bin/sh", "-c", "kill -TERM 1 && sleep 90"]
---
# Prevent all pods from being evicted at once
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: factory-task-executor-pdb
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: factory-task-executor
```

## Cost Savings

Example spot pricing (as of 2024):

| Instance Type | On-Demand | Spot (avg) | Savings |
|---------------|-----------|------------|---------|
| AWS c6i.4xlarge | $0.68/hr | $0.20/hr | 71% |
| GCP n2-standard-16 | $0.78/hr | $0.19/hr | 76% |
| Azure F16s v2 | $0.72/hr | $0.17/hr | 76% |

For a factory running 24/7 with 10 workers:
- On-demand: $0.70/hr × 10 × 24 × 30 = **$5,040/month**
- Spot: $0.20/hr × 10 × 24 × 30 = **$1,440/month**
- **Savings: $3,600/month (71%)**

## Interruption Rates

Typical spot interruption rates:

- **AWS EC2 Spot**: 5-15% of instances per month (< 5% for most instance types)
- **GCP Preemptible**: 24-hour maximum lifetime (guaranteed interruption)
- **Azure Spot**: Similar to AWS, varies by region

With per-turn checkpointing, interruptions only lose:
- Work since the last checkpoint (< 1 turn, typically < 30 seconds)
- Time to reschedule and resume (typically < 10 seconds)

## Best Practices

1. **Checkpoint frequently**: After every turn, not just at task end
2. **Test interruptions**: Use `kubectl delete pod` to simulate pre-emption
3. **Monitor resume success**: Track checkpoint → resume latency
4. **Diversify instance types**: Use multiple spot pools to reduce interruptions
5. **Fallback to on-demand**: For critical paths, have on-demand capacity available
6. **Graceful shutdown window**: Set `terminationGracePeriodSeconds` ≥ 90s
7. **Heartbeat checkpoints**: Send checkpoint IDs in Temporal heartbeats
8. **Idempotent tools**: Design tools to be safely retried (e.g., git operations)

## Monitoring

Track spot interruption impact:

```promql
# Spot interruption rate
rate(factory_task_preempted_total[1h])

# Resume success rate
rate(factory_task_resumed_total[1h]) 
  / 
rate(factory_task_preempted_total[1h])

# Work lost per interruption (should be < 1 turn)
histogram_quantile(0.95, factory_checkpoint_age_seconds)
```

## Testing

Simulate pre-emption locally:

```python
# tests/test_preemption.py
def test_checkpoint_resume():
    harness = create_harness(task)
    
    # Run for 3 turns
    for _ in range(3):
        harness.step()
    
    # Simulate pre-emption: checkpoint and discard
    checkpoint_id = harness.checkpoint()
    del harness
    
    # Resume from checkpoint
    state = checkpoint_store.load(task.id, task.work_order_id)
    harness2 = AgentHarness.from_checkpoint(state)
    
    # Continue to completion
    result = harness2.resume(task)
    assert result.status == TaskStatus.SUCCEEDED
```

## Further Reading

- [AWS Spot Best Practices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-best-practices.html)
- [GCP Preemptible VMs](https://cloud.google.com/compute/docs/instances/preemptible)
- [Temporal Retry Policies](https://docs.temporal.io/activities#retry-policy)
- [Kubernetes Pod Disruption Budgets](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/)
