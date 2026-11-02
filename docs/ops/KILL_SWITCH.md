# Kill Switch Operations Guide

The kill switch provides global and per-tenant pause/resume controls for the software factory.
This is critical for emergency stops, maintenance windows, and tenant quota enforcement.

## Overview

The kill switch operates at two levels:

1. **Global**: Stops all new work across the entire factory
2. **Per-tenant**: Stops new work for a specific tenant while others continue

When paused, the factory:
- Stops scheduling new work orders and tasks
- Signals in-flight tasks to checkpoint and gracefully stop
- Preserves all state for resumption

## CLI Commands

### Pause the Factory

Global pause (stops everything):

```bash
factory control pause \
  --reason "Emergency maintenance" \
  --user "ops-team"
```

Per-tenant pause:

```bash
factory control pause \
  --tenant acme-corp \
  --reason "Quota exceeded" \
  --user "billing-system"
```

### Resume the Factory

Global resume:

```bash
factory control resume
```

Per-tenant resume:

```bash
factory control resume --tenant acme-corp
```

### Check Status

Check all kill switches:

```bash
factory control status
```

Check specific tenant:

```bash
factory control status --tenant acme-corp
```

## Integration with Task Execution

The harness should check the kill switch before starting each turn:

```python
from factory.control import KillSwitch
from factory.control.kill_switch import FileKillSwitchStore

store = FileKillSwitchStore(Path(".factory-runs/kill_switch"))
switch = KillSwitch(store)

if switch.is_paused(tenant_id=task.tenant_id):
    # Checkpoint current state
    checkpoint_store.save(run_state)
    
    # Return a special result that indicates pause
    return TaskResult(
        task_id=task.id,
        status=TaskStatus.PENDING,
        summary="Checkpointed due to kill switch pause",
        checkpoint_id=checkpoint_id,
    )
```

## Use Cases

### Emergency Stop

When a critical issue is detected:

1. Pause globally: `factory control pause --reason "Incident INC-123: model gateway outage"`
2. Investigate and resolve the issue
3. Resume: `factory control resume`

### Planned Maintenance

Before infrastructure changes:

1. Pause: `factory control pause --reason "Planned maintenance window"`
2. Wait for in-flight tasks to checkpoint (monitor via orchestrator)
3. Perform maintenance
4. Resume: `factory control resume`

### Tenant Quota Enforcement

When a tenant exceeds their budget:

1. Pause tenant: `factory control pause --tenant acme-corp --reason "Monthly budget exceeded"`
2. Contact tenant for resolution
3. Resume when approved: `factory control resume --tenant acme-corp`

### Rate Limiting

Temporarily throttle a tenant during load spikes:

1. Pause: `factory control pause --tenant high-volume-tenant --reason "Rate limiting"`
2. Wait for load to normalize
3. Resume: `factory control resume --tenant high-volume-tenant`

## State Storage

Kill switch state is stored in files under `.factory-runs/kill_switch/`:

```
.factory-runs/kill_switch/
├── global.json              # Global kill switch state
└── tenants/
    ├── acme-corp.json       # Per-tenant state
    └── widgets-inc.json
```

Each state file contains:

```json
{
  "state": "paused",
  "paused_at": "2024-03-15T14:30:00Z",
  "paused_by": "ops-team",
  "reason": "Emergency maintenance",
  "tenant_id": null
}
```

## Production Deployment

For production deployments, replace `FileKillSwitchStore` with a distributed store:

- **Redis**: For low-latency checks across multiple workers
- **etcd**: For strong consistency in Kubernetes deployments
- **DynamoDB**: For AWS-native deployments

The `KillSwitchStore` protocol makes swapping backends straightforward:

```python
class RedisKillSwitchStore:
    def __init__(self, redis_client):
        self.redis = redis_client
    
    def get_global(self) -> KillSwitchStatus:
        data = self.redis.get("factory:killswitch:global")
        if not data:
            return KillSwitchStatus()
        return KillSwitchStatus.model_validate_json(data)
    
    # ... implement other methods
```

## Monitoring

Set up alerts for:

1. **Pause events**: Alert ops when any kill switch is activated
2. **Long pauses**: Warn if paused for > 1 hour (may indicate forgotten state)
3. **Automatic pauses**: Track budget-triggered pauses for capacity planning

Example Prometheus alert:

```yaml
- alert: FactoryPaused
  expr: factory_killswitch_paused{scope="global"} == 1
  for: 5m
  annotations:
    summary: "Factory is paused globally"
```

## Best Practices

1. **Always provide a reason**: Makes auditing and troubleshooting easier
2. **Document operator**: Use `--user` to track who initiated the pause
3. **Test regularly**: Include kill switch drills in runbooks
4. **Monitor checkpointing**: Ensure tasks checkpoint successfully before pausing
5. **Automate tenant pauses**: Integrate with billing/quota systems
6. **Set up monitoring**: Alert on pause events and long-running pauses
