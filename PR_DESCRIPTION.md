# Phase 2a: Temporal Cluster Infrastructure

This PR implements the complete Temporal cluster infrastructure and worker deployment configuration for Phase 2a of the Software Factory.

## Overview

Stream C deliverable: Production-ready Kubernetes manifests and operator documentation for deploying the Temporal cluster and factory workers.

## What's Included

### 1. Temporal Cluster (`infra/temporal/`)

Complete Kubernetes manifests for deploying Temporal:

- **Base manifests** (`base/`):
  - Namespace definition
  - PostgreSQL StatefulSet with persistent storage
  - Temporal server (Frontend, History, Matching, Worker services)
  - Kubernetes Services for all components
  - Admin tools pod for tctl operations
  - ConfigMaps for Temporal configuration

- **Production overlay** (`overlays/production/`):
  - Scaled replicas (Frontend: 3, History: 5, Matching: 3)
  - Production resource limits
  - 100Gi PostgreSQL storage
  - Kustomize patches for production tuning

### 2. Worker Deployment (`infra/workers/`)

Factory worker Kubernetes manifests:

- **Base manifests** (`base/`):
  - Worker Deployment with health checks
  - ServiceAccount, Role, RoleBinding for sandbox operations
  - ConfigMap for worker configuration
  - HorizontalPodAutoscaler (2-10 replicas)
  - Prometheus metrics exposure

- **Production overlay** (`overlays/production/`):
  - 5-20 replicas with autoscaling
  - Enhanced resource limits (4 CPU, 8Gi RAM)

- **Development overlay** (`overlays/development/`):
  - Single replica with debug logging

- **Dockerfile**: Multi-stage build for factory workers

### 3. Documentation (`docs/ops/`)

Comprehensive operator documentation:

- **TEMPORAL_OPERATIONS.md**: Complete operations guide covering:
  - Prerequisites and cluster requirements
  - Step-by-step deployment procedures
  - Health checks and verification
  - Common operations (namespace management, workflow operations)
  - Monitoring and observability setup
  - Troubleshooting guides
  - Backup and recovery procedures
  - Scaling recommendations
  - Upgrade procedures

### 4. Quick Start

- **infra/QUICKSTART.md**: 5-minute deployment guide
- **infra/README.md**: Infrastructure overview and architecture

### 5. CI/CD

- **GitHub Actions workflow** (`.github/workflows/validate-infra.yml`):
  - Validates all Kubernetes manifests with kustomize
  - Checks for hardcoded secrets
  - Builds Dockerfile to verify it works
  - Runs on PR and merge to main

## Key Features

### Security
- ✅ Non-root container execution
- ✅ Read-only root filesystems
- ✅ No privileged escalation
- ✅ Secrets via Kubernetes secrets (not hardcoded)
- ✅ RBAC with least-privilege ServiceAccounts
- ✅ Network isolation ready

### High Availability
- ✅ Multiple replicas of all Temporal services
- ✅ Pod anti-affinity for distribution
- ✅ HorizontalPodAutoscaler for workers
- ✅ Rolling update strategy with zero downtime

### Observability
- ✅ Prometheus metrics on all components
- ✅ Structured JSON logging
- ✅ Health and readiness probes
- ✅ OpenTelemetry integration points

### Production Ready
- ✅ Resource requests and limits tuned
- ✅ Persistent storage for PostgreSQL
- ✅ Init containers for database schema setup
- ✅ Graceful shutdown handling
- ✅ Kustomize overlays for environment-specific configuration

## Architecture

```
┌────────────────────────────────────────────────┐
│ Software Factory (Kubernetes Namespace)         │
│                                                │
│  ┌──────────────┐       ┌──────────────────┐  │
│  │   Workers    │──────▶│ Temporal Frontend│  │
│  │ (5-20 pods)  │ gRPC  │   (3 replicas)   │  │
│  └──────────────┘       └────────┬─────────┘  │
│                                  │             │
│              ┌───────────────────┼──────────┐  │
│              ▼                   ▼          ▼  │
│         ┌────────┐         ┌─────────┐  ┌───┐ │
│         │History │         │Matching │  │...│ │
│         │(5 reps)│         │(3 reps) │  └───┘ │
│         └───┬────┘         └────┬────┘        │
│             │                   │             │
│             └───────┬───────────┘             │
│                     ▼                         │
│              ┌─────────────┐                  │
│              │ PostgreSQL  │                  │
│              │ StatefulSet │                  │
│              │   (100Gi)   │                  │
│              └─────────────┘                  │
└────────────────────────────────────────────────┘
```

## Resource Requirements

**Production:**
- 32 CPU cores
- 64Gi RAM
- 200Gi storage (PostgreSQL + worker ephemeral)

**Development:**
- 8 CPU cores
- 16Gi RAM
- 50Gi storage

## Deployment

### Quick Start (5 minutes)
```bash
# Create namespace and secrets
kubectl create namespace software-factory
kubectl create secret generic temporal-postgres-secret \
  --from-literal=password="$(openssl rand -base64 32)" \
  --namespace=software-factory

# Deploy production configuration
kubectl apply -k infra/temporal/overlays/production
kubectl apply -k infra/workers/overlays/production

# Verify
kubectl wait --for=condition=ready pod -l app=temporal -n software-factory
kubectl exec -it temporal-admin-tools -n software-factory -- tctl cluster health
```

See [infra/QUICKSTART.md](infra/QUICKSTART.md) for the complete quick start guide.

### Full Deployment
See [docs/ops/TEMPORAL_OPERATIONS.md](docs/ops/TEMPORAL_OPERATIONS.md) for:
- Detailed prerequisites
- Production deployment procedures
- Worker image building
- Configuration customization
- Monitoring setup

## Alignment with Implementation Plan

This PR implements **Phase 2: Core Pipeline** requirements from [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md):

✅ **Section 6: Cloud Platform**
- Kubernetes manifests for Temporal (self-hosted deployment)
- Worker deployment with autoscaling
- PostgreSQL for state persistence
- RBAC and security configurations

✅ **ADR-0001: Durable Workflow Orchestration**
- Temporal cluster as the durable workflow engine
- Activity-based task execution
- Checkpoint and resume capabilities via Temporal

## Testing

All manifests validated in CI:
- ✅ `kustomize build` succeeds for all overlays
- ✅ `kubectl apply --dry-run=client` passes
- ✅ No hardcoded secrets detected
- ✅ Dockerfile builds successfully

## What's NOT Included (Other Streams)

As specified in the task constraints, this PR does NOT include:
- ❌ GitHub App code (other stream)
- ❌ Sandbox pod spawn implementation in Python (other stream)
- ❌ Phase 2b policy/approvals (future phase)
- ❌ docs/GETTING_STARTED.md changes (other stream)

These are owned by parallel agents or future phases.

## Next Steps (After Merge)

1. **Deploy to a test cluster** using the quick start guide
2. **Implement worker Python code** (`factory/worker.py`) to connect to Temporal
3. **Implement Temporal workflows** for Supervisor orchestration
4. **Set up monitoring** (Prometheus, Grafana dashboards)
5. **Configure backups** for PostgreSQL

## References

- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) - Overall implementation plan
- [docs/adr/0001-durable-workflow-orchestration.md](docs/adr/0001-durable-workflow-orchestration.md) - Why Temporal
- [Temporal Documentation](https://docs.temporal.io/self-hosted-guide) - Official self-hosted guide
- [Kubernetes Documentation](https://kubernetes.io/docs/) - Kubernetes reference

## Review Checklist

- [x] Kubernetes manifests are valid YAML
- [x] Kustomize builds succeed for all overlays
- [x] No hardcoded secrets (template file provided)
- [x] Resource requests and limits defined
- [x] Health and readiness probes configured
- [x] RBAC follows least-privilege principle
- [x] Documentation is comprehensive and accurate
- [x] CI workflow validates all manifests
- [x] Architecture aligns with Implementation Plan
- [x] Secure by default (non-root, read-only FS)

---

**Ready for Review**

This PR is production-ready pending:
1. Test deployment validation
2. Team review of resource allocations
3. Security team approval of RBAC policies

Deploy confidence: **High** ✅
