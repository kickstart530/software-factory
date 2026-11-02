# Temporal Cluster Infrastructure

This directory contains Kubernetes manifests for deploying the Temporal cluster that orchestrates factory workflows.

## Structure

```
temporal/
├── base/                      # Base Kubernetes manifests (namespace-agnostic)
│   ├── namespace.yaml         # Temporal namespace
│   ├── postgres.yaml          # PostgreSQL StatefulSet for Temporal persistence
│   ├── server.yaml            # Temporal server deployment
│   ├── admin-tools.yaml       # Admin tools pod for tctl operations
│   └── services.yaml          # Services for Temporal components
├── overlays/
│   └── production/            # Production-specific overlays (resource limits, replicas)
│       ├── kustomization.yaml
│       └── patches/
└── README.md                  # This file

## Quick Start

See [docs/ops/TEMPORAL_OPERATIONS.md](../../docs/ops/TEMPORAL_OPERATIONS.md) for detailed operator instructions.

## Components

### Temporal Server
- Frontend service (gRPC API)
- History service (workflow execution)
- Matching service (task queue management)
- Worker service (internal workflows)

### PostgreSQL
- StatefulSet with persistent storage
- Database initialization via init containers
- Separate databases for default, visibility, and system namespaces

### Admin Tools
- `tctl` command-line interface
- Operational pod for cluster management and troubleshooting

## Network Architecture

```
┌─────────────────────────────────────────────────────┐
│ Factory Namespace (software-factory)                 │
│                                                      │
│  ┌──────────────┐                                   │
│  │ Worker Pods  │──────────┐                        │
│  │ (Python SDK) │          │                        │
│  └──────────────┘          │                        │
│                            │ gRPC                   │
│                            ▼                        │
│  ┌──────────────────────────────────────┐           │
│  │ Temporal Frontend Service            │           │
│  │ (temporal-frontend:7233)             │           │
│  └──────────────┬───────────────────────┘           │
│                 │                                    │
│     ┌───────────┼────────────┐                      │
│     ▼           ▼            ▼                      │
│ ┌────────┐ ┌─────────┐ ┌──────────┐                │
│ │History │ │Matching │ │Internal  │                │
│ │Service │ │Service  │ │Worker    │                │
│ └───┬────┘ └────┬────┘ └────┬─────┘                │
│     │           │           │                       │
│     └───────────┴───────────┘                       │
│                 │                                    │
│                 ▼                                    │
│     ┌──────────────────────┐                        │
│     │ PostgreSQL           │                        │
│     │ (StatefulSet)        │                        │
│     └──────────────────────┘                        │
└─────────────────────────────────────────────────────┘
```

## Storage Requirements

- **PostgreSQL PVC**: 100Gi (production) / 20Gi (dev)
- **Temporal Server ephemeral**: 10Gi

## Resource Requests

See [overlays/production/](overlays/production/) for production resource limits.

Minimum requirements:
- Frontend: 0.5 CPU, 512Mi RAM
- History: 1 CPU, 1Gi RAM
- Matching: 0.5 CPU, 512Mi RAM
- Worker: 0.5 CPU, 512Mi RAM
- PostgreSQL: 2 CPU, 4Gi RAM

## Security

- No credentials in manifests; use Kubernetes secrets
- PostgreSQL password mounted from `temporal-postgres-secret`
- Workload identity for cloud provider integrations
- Network policies: workers → frontend only; internal services isolated

## References

- [Temporal Self-Hosted Guide](https://docs.temporal.io/self-hosted-guide)
- [Temporal Kubernetes Deployment](https://github.com/temporalio/helm-charts)
- [ADR-0001: Durable Workflow Orchestration](../../docs/adr/0001-durable-workflow-orchestration.md)
