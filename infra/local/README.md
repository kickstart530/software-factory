# Local Development Environment

This directory contains scripts and manifests to run the software factory locally using `kind` (Kubernetes in Docker).

## Quick Start

```bash
# Install prerequisites (if not already installed)
./infra/local/install-prereqs.sh

# Bring up the local cluster
./infra/local/up.sh

# Verify the environment
./infra/local/verify.sh

# Run the hello-world sandbox example
./infra/local/run-hello-world.sh

# Tear down the cluster
./infra/local/down.sh
```

## What Gets Deployed

- **kind cluster** with one control plane and two worker nodes
- **Namespace structure**: `factory-system`, `factory-sandboxes`
- **PostgreSQL** (single pod for development)
- **MinIO** (object storage compatible with S3 API)
- **OpenTelemetry Collector** (configured for local Jaeger)
- **Jaeger** (trace UI at http://localhost:16686)
- **gVisor runtime class** (stub/documented fallback for systems without gVisor support)

## System Requirements

- Docker Desktop or Docker Engine
- 8GB+ RAM allocated to Docker
- 20GB+ disk space

## Differences from Production

- Single-replica databases (no HA)
- EmptyDir or hostPath storage (data is ephemeral)
- No TLS between components
- Permissive network policies
- gVisor may fall back to runc if not available (see notes in manifests)
