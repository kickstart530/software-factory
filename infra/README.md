# Infrastructure

Kubernetes manifests and deployment configuration for the Software Factory platform.

## Contents

- **temporal/**: Temporal cluster deployment (workflow orchestration)
- **workers/**: Factory worker deployments (task execution)
- **QUICKSTART.md**: Fast deployment guide

## Architecture

```
┌─────────────────────────────────────────────────┐
│ Software Factory (software-factory namespace)    │
│                                                 │
│  ┌──────────────┐         ┌─────────────────┐  │
│  │   Workers    │────────▶│ Temporal Cluster│  │
│  │ (Task Exec)  │  gRPC   │   (Frontend)    │  │
│  └──────────────┘         └────────┬────────┘  │
│                                    │            │
│                   ┌────────────────┼────────┐   │
│                   ▼                ▼        ▼   │
│              ┌────────┐     ┌─────────┐ ┌────┐ │
│              │History │     │Matching │ │...  │ │
│              └───┬────┘     └────┬────┘ └────┘ │
│                  │               │              │
│                  └───────┬───────┘              │
│                          ▼                      │
│                   ┌─────────────┐               │
│                   │ PostgreSQL  │               │
│                   │(StatefulSet)│               │
│                   └─────────────┘               │
└─────────────────────────────────────────────────┘
```

## Deployment Paths

### Development
```bash
kubectl apply -k temporal/base
kubectl apply -k workers/base
```

### Production
```bash
kubectl apply -k temporal/overlays/production
kubectl apply -k workers/overlays/production
```

## Documentation

- [QUICKSTART.md](QUICKSTART.md) - Quick deployment guide
- [temporal/README.md](temporal/README.md) - Temporal cluster details
- [workers/README.md](workers/README.md) - Worker deployment details
- [docs/ops/TEMPORAL_OPERATIONS.md](../docs/ops/TEMPORAL_OPERATIONS.md) - Complete operations guide

## Components

### Temporal Cluster
- **Frontend**: gRPC API (3 replicas prod, 2 dev)
- **History**: Workflow execution (5 replicas prod, 3 dev)
- **Matching**: Task queue management (3 replicas prod, 2 dev)
- **Worker**: Internal workflows (1 replica)
- **PostgreSQL**: State persistence (1 StatefulSet, 100Gi prod)

### Factory Workers
- **Task Executors**: Run agent tasks (5-20 replicas prod with HPA)
- **Sandbox Managers**: Manage ephemeral sandboxes

## Resource Requirements

**Minimum (Development):**
- 8 CPU cores
- 16Gi RAM
- 50Gi storage

**Recommended (Production):**
- 32 CPU cores
- 64Gi RAM
- 200Gi storage

## Security

- Non-root container execution
- Read-only root filesystems
- No privileged escalation
- Network policies (workers → frontend only)
- Workload identity for cloud access
- Secrets managed via Kubernetes secrets

## Monitoring

All components expose Prometheus metrics on port 9090:
- `temporal_request_latency`
- `temporal_workflow_*`
- `factory_task_*`
- `factory_worker_*`

## Support

See [docs/ops/TEMPORAL_OPERATIONS.md](../docs/ops/TEMPORAL_OPERATIONS.md) for:
- Detailed deployment procedures
- Troubleshooting guides
- Scaling recommendations
- Backup and recovery procedures
- Upgrade procedures

## References

- [Implementation Plan](../docs/IMPLEMENTATION_PLAN.md)
- [ADR-0001: Durable Workflow Orchestration](../docs/adr/0001-durable-workflow-orchestration.md)
- [Temporal Documentation](https://docs.temporal.io/)
