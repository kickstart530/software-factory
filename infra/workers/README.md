# Factory Workers

Kubernetes manifests for deploying Temporal workers that execute factory agent tasks.

## Overview

Workers are Python applications using the Temporal Python SDK. Each worker:
- Polls task queues from the Temporal cluster
- Executes agent tasks in sandboxed environments
- Reports results and checkpoints back to workflows

## Structure

```
workers/
├── base/
│   ├── deployment.yaml       # Worker deployment
│   ├── rbac.yaml            # ServiceAccount, Role, RoleBinding
│   └── configmap.yaml       # Worker configuration
├── overlays/
│   ├── production/          # Production scaling and resources
│   └── development/         # Dev environment overrides
└── README.md                # This file
```

## Worker Types

### Task Executor Workers
- **Queue**: `factory-tasks`
- **Purpose**: Execute agent tasks (Implementer, Reviewer, Planner, etc.)
- **Scaling**: HPA based on queue depth and CPU
- **Resources**: Variable CPU/memory based on agent requirements

### Sandbox Manager Workers
- **Queue**: `factory-sandbox-ops`
- **Purpose**: Create, monitor, and clean up agent sandboxes
- **Scaling**: Fixed replicas (lower volume)
- **Resources**: Lower footprint, network-bound

## Configuration

Workers are configured via:
1. **ConfigMap**: Non-sensitive configuration (cluster address, queues, concurrency)
2. **Secrets**: Temporal connection credentials, cloud provider credentials
3. **Environment Variables**: Runtime overrides

## Deployment

```bash
# Development
kubectl apply -k workers/base

# Production
kubectl apply -k workers/overlays/production
```

## Monitoring

- **Metrics**: Prometheus metrics exposed on `:9090/metrics`
- **Logging**: JSON structured logs to stdout
- **Tracing**: OpenTelemetry spans for each activity
- **Health**: Liveness and readiness probes on `:8080/health`

## Sandboxing

Workers spawn agent sandboxes using one of:
- **gVisor** (RuntimeClass: gvisor)
- **Kata Containers** (RuntimeClass: kata)
- **Standard** (RuntimeClass: default, development only)

See [docs/ops/SANDBOX_ARCHITECTURE.md](../../docs/ops/SANDBOX_ARCHITECTURE.md) for sandbox implementation details.

## Autoscaling

Production workers use HorizontalPodAutoscaler:
- **Min replicas**: 2
- **Max replicas**: 20
- **Target CPU**: 70%
- **Target Memory**: 80%
- **Custom metric**: Temporal queue depth (via Prometheus adapter)

## Resource Quotas

Per worker pod:
- **Development**: 1 CPU, 2Gi RAM
- **Production**: 2 CPU, 4Gi RAM (request), 4 CPU, 8Gi RAM (limit)

## Security

- Non-root container execution
- Read-only root filesystem
- No privileged escalation
- Workload identity for cloud credentials
- Network policies: Temporal frontend only

## References

- [Temporal Python SDK Workers](https://docs.temporal.io/dev-guide/python/foundations#develop-workers)
- [Worker Tuning Guide](https://docs.temporal.io/dev-guide/worker-performance)
