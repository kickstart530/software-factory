# Operations Guide: Phase 0 Infrastructure

This guide covers deploying and operating the Phase 0 infrastructure foundations for the software factory.

## Overview

Phase 0 provides the foundational infrastructure that all other components build on:

- **Kubernetes cluster** (local via kind, or cloud via Terraform)
- **PostgreSQL** for persistent state
- **Object storage** (MinIO locally, S3/GCS/Blob in cloud)
- **OpenTelemetry collector** for observability
- **Jaeger** for trace visualization
- **Runtime isolation** (gVisor or documented fallback)
- **Sandbox execution environment** (hello-world proof of concept)

## Deployment Options

### Option 1: Local Development (kind)

Best for: Development, testing, quick iteration

**Prerequisites:**
- Docker Desktop or Docker Engine (8GB+ RAM)
- 20GB+ disk space
- Linux, macOS, or WSL2

**Quick Start:**
```bash
# Install prerequisites
./infra/local/install-prereqs.sh

# Bring up the cluster
./infra/local/up.sh

# Verify deployment
./infra/local/verify.sh

# Run hello-world sandbox
./infra/local/run-hello-world.sh

# Clean up
./infra/local/down.sh
```

**Access:**
- Jaeger UI: http://localhost:16686
- PostgreSQL: `localhost:5432` (user: `factory`, password: `factory-dev`, db: `factory`)
- MinIO Console: http://localhost:9001 (user: `minioadmin`, password: `minioadmin`)

**Limitations:**
- Single-replica databases (no HA)
- Ephemeral storage (data lost on cluster restart)
- No TLS
- gVisor may not be available (falls back to runc)

### Option 2: Cloud Deployment (AWS/GCP/Azure)

Best for: Staging, production, multi-tenant scenarios

**Prerequisites:**
- Cloud account with appropriate permissions
- Terraform >= 1.5
- kubectl
- Cloud CLI (aws-cli, gcloud, or az)

**AWS Example:**
```bash
cd infra/terraform/aws

# Configure backend (one-time)
cp backend.tf.example backend.tf
# Edit backend.tf with your S3 bucket

# Configure environment
cp environments/dev.tfvars.example environments/dev.tfvars
# Edit environments/dev.tfvars

# Deploy infrastructure
terraform init
terraform plan -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars

# Configure kubectl
aws eks update-kubeconfig --region <region> --name <cluster-name>

# Deploy Kubernetes resources
kubectl apply -f ../../k8s/base/

# Verify
kubectl get pods -n factory-system
```

See `infra/terraform/README.md` for GCP and Azure instructions.

## Architecture

### Namespaces

- `factory-system`: Core infrastructure (Postgres, MinIO/S3, OTel, Jaeger)
- `factory-sandboxes`: Ephemeral agent sandbox pods

### Network Flow

```
Sandbox Pod (factory-sandboxes)
    │
    ├─> OpenTelemetry Collector (factory-system:4317)
    │       └─> Jaeger (factory-system:16686)
    │
    ├─> PostgreSQL (factory-system:5432)
    │
    └─> Object Storage (MinIO/S3)
```

### Storage

**Local (kind):**
- PostgreSQL: PersistentVolumeClaim (2Gi)
- MinIO: EmptyDir (ephemeral)

**Cloud:**
- PostgreSQL: Managed RDS/Cloud SQL/Azure Database
- Object Storage: S3/GCS/Azure Blob with lifecycle policies

## Observability

### Tracing

**View traces:**
1. Open Jaeger UI (http://localhost:16686 for local)
2. Select service: `factory.sandbox.hello-world`
3. Click "Find Traces"

**Trace retention:**
- Local: In-memory (lost on restart)
- Cloud: Configure external storage (S3, Cassandra, Elasticsearch)

**Example trace structure:**
```
hello_world_task (root span)
├─ gather_system_info
├─ simulate_work
└─ emit_result
```

### Logs

**View sandbox logs:**
```bash
# List sandbox pods
kubectl get pods -n factory-sandboxes

# View logs
kubectl logs -n factory-sandboxes <pod-name>

# Stream logs
kubectl logs -n factory-sandboxes <pod-name> --follow
```

**Log format:**
```
[2026-11-02T01:20:00.000000] INFO: Starting hello-world sandbox task
```

### Metrics

Phase 0 includes basic metrics collection via OTel. Metrics are logged but not yet exported to a time-series database. Future phases will add:
- Prometheus for metrics storage
- Grafana for dashboards
- Alerting rules

## Runtime Isolation

### gVisor (Recommended)

gVisor provides user-space kernel isolation for enhanced security.

**Setup (Linux):**
```bash
# Install runsc
curl -fsSL https://gvisor.dev/archive.key | sudo apt-key add -
sudo add-apt-repository "deb https://storage.googleapis.com/gvisor/releases release main"
sudo apt-get update && sudo apt-get install -y runsc

# Configure containerd
sudo tee -a /etc/containerd/config.toml <<EOF
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"
EOF

sudo systemctl restart containerd
```

**Verify:**
```bash
kubectl run test-gvisor --rm -it --image=alpine --restart=Never \
  --overrides='{"spec":{"runtimeClassName":"gvisor"}}' -- dmesg | grep gVisor
```

**Fallback:**
If gVisor is not available, sandboxes run with the default runc runtime. Security relies on:
- Namespace isolation
- Network policies
- Security contexts (runAsNonRoot, readOnlyRootFilesystem where possible)
- Resource limits

### Security Context

All sandbox pods enforce:
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  capabilities:
    drop: [ALL]
```

## Hello-World Sandbox

The hello-world sandbox validates the complete infrastructure stack.

**What it tests:**
- Pod scheduling in factory-sandboxes namespace
- Tracing pipeline (Pod → OTel → Jaeger)
- Structured logging
- Resource limits
- Network connectivity
- Security context enforcement

**Run it:**
```bash
# Local
./infra/local/run-hello-world.sh

# Cloud
docker build -t factory-sandbox-hello:latest -f sandbox/Dockerfile sandbox/
docker push <registry>/factory-sandbox-hello:latest
# Update infra/k8s/sandbox/hello-world-job.yaml with your registry
kubectl apply -f infra/k8s/sandbox/hello-world-job.yaml
kubectl logs -n factory-sandboxes -l job-name=hello-world-sandbox --follow
```

**Expected output:**
```
[timestamp] INFO: Initializing hello-world sandbox
[timestamp] INFO: Tracing configured
[timestamp] INFO: Starting hello-world task
[timestamp] INFO: System info: {...}
[timestamp] INFO: Work unit 1/3 complete
[timestamp] INFO: Work unit 2/3 complete
[timestamp] INFO: Work unit 3/3 complete
[timestamp] INFO: Task result: {"status": "success", ...}
[timestamp] INFO: Hello-world task completed successfully
[timestamp] INFO: Traces flushed
```

**Verify trace:**
1. Open Jaeger UI
2. Service: `factory.sandbox.hello-world`
3. Should see spans: `hello_world_task`, `gather_system_info`, `simulate_work`, `emit_result`

## Troubleshooting

### Cluster won't start

**Symptom:** `kind create cluster` fails

**Solution:**
- Check Docker is running: `docker ps`
- Ensure port conflicts: `lsof -i :16686` (Jaeger port)
- Check Docker resources: Increase memory to 8GB+ in Docker Desktop settings

### Pods stuck in Pending

**Symptom:** Pods in `Pending` state

**Check:**
```bash
kubectl describe pod <pod-name> -n factory-system
```

**Common causes:**
- Insufficient resources: Scale up node pool or adjust pod resource requests
- Image pull errors: Check image name and registry credentials
- Node selector/affinity: Verify node labels match pod requirements

### OTel collector not receiving traces

**Symptom:** No traces in Jaeger

**Check:**
```bash
# OTel collector logs
kubectl logs -n factory-system -l app=otel-collector

# Verify service endpoint
kubectl get svc otel-collector -n factory-system
```

**Solution:**
- Verify `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable in sandbox pod
- Check network policies allow traffic from factory-sandboxes to factory-system
- Ensure OTel collector service is ClusterIP and reachable

### PostgreSQL connection refused

**Symptom:** Cannot connect to database

**Check:**
```bash
# PostgreSQL logs
kubectl logs -n factory-system -l app=postgres

# Verify service
kubectl get svc postgres -n factory-system

# Test connection from a pod
kubectl run -it --rm psql --image=postgres:16-alpine --restart=Never -- \
  psql -h postgres.factory-system -U factory -d factory
```

### gVisor runtime not found

**Symptom:** Pods fail to schedule with RuntimeClass error

**Solution:**
1. Verify RuntimeClass exists: `kubectl get runtimeclass gvisor`
2. Check node configuration: gVisor must be installed on worker nodes
3. **Fallback:** Remove `runtimeClassName: gvisor` from pod specs

## Maintenance

### Backup

**Local:** Not applicable (ephemeral environment)

**Cloud:**

PostgreSQL:
- Automated backups enabled via Terraform (7-day retention)
- Manual snapshot: AWS console or `aws rds create-db-snapshot`

Object Storage:
- Versioning enabled
- Lifecycle policies archive old data to Glacier/Coldline after 90 days

### Upgrades

**Kubernetes:**
```bash
# Check current version
kubectl version --short

# Upgrade (cloud-specific)
# AWS: Update eks_cluster_version in terraform variables
# GCP: gcloud container clusters upgrade
# Azure: az aks upgrade
```

**Component updates:**
```bash
# Update image versions in manifests
vi infra/k8s/base/<component>.yaml

# Apply changes
kubectl apply -f infra/k8s/base/
```

### Scaling

**System nodes:**
```bash
# AWS: Update system_node_desired_size in tfvars
terraform apply -var-file=environments/dev.tfvars
```

**Sandbox nodes:**
- Autoscaling configured based on pending pod count
- Adjust min/max in Terraform variables: `sandbox_node_min_size`, `sandbox_node_max_size`

### Cost Monitoring

**AWS:**
```bash
# Tag filtering
aws ce get-cost-and-usage --time-period Start=2026-11-01,End=2026-11-30 \
  --granularity MONTHLY --metrics BlendedCost \
  --filter file://cost-filter.json
```

**Optimization:**
- Use spot instances for sandbox nodes (enabled by default)
- Right-size PostgreSQL instance (start with db.t3.micro)
- Enable S3 lifecycle policies (included in Terraform)

## Phase 0 Exit Criteria

✅ **IaC exists:** Terraform for cloud, kind scripts for local
✅ **Cluster bootstrap:** `terraform apply` or `./infra/local/up.sh` brings up environment
✅ **Namespaces:** factory-system and factory-sandboxes created
✅ **Runtime class:** gVisor configured (or documented stub)
✅ **Object store:** S3/MinIO operational
✅ **PostgreSQL:** Database available
✅ **OTel collector:** Receiving and forwarding traces
✅ **Hello-world sandbox:** Runs and emits trace visible in Jaeger
✅ **Ops docs:** This document

## Next Steps

With Phase 0 complete, proceed to:

1. **Phase 1:** Agent harness hardening (golden task set, replay, OTel exporter integration)
2. **Phase 2:** Temporal workflows, GitHub App, tool gateway, sandbox spawner
3. **Operational:** Run evals, monitor costs, tune autoscaling

## Support

**Local issues:** See `infra/local/README.md`
**Cloud issues:** See `infra/terraform/<cloud>/README.md`
**Sandbox issues:** See `sandbox/README.md`
