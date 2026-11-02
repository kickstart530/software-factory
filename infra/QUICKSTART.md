# Quick Start: Temporal Cluster Deployment

This guide provides the fastest path to deploying the Temporal cluster and workers for the Software Factory.

## Prerequisites

- Kubernetes cluster (1.25+) with kubectl configured
- Storage class with dynamic provisioning
- At least 16 CPU cores and 32Gi RAM available

## Deploy in 5 Minutes

### 1. Create Namespace and Secrets

```bash
# Create namespace
kubectl create namespace software-factory

# Generate and store PostgreSQL password
export POSTGRES_PASSWORD=$(openssl rand -base64 32)
echo "Save this password: $POSTGRES_PASSWORD"

# Create secret
kubectl create secret generic temporal-postgres-secret \
  --from-literal=password="$POSTGRES_PASSWORD" \
  --namespace=software-factory
```

### 2. Deploy Temporal Cluster

```bash
# Clone repository
git clone https://github.com/kickstart530/software-factory.git
cd software-factory

# Deploy base configuration (development)
kubectl apply -k infra/temporal/base

# OR deploy production configuration
kubectl apply -k infra/temporal/overlays/production
```

### 3. Wait for Cluster Ready

```bash
# Wait for PostgreSQL
kubectl wait --for=condition=ready pod -l app=postgres \
  -n software-factory --timeout=300s

# Wait for Temporal services
kubectl wait --for=condition=ready pod -l app=temporal \
  -n software-factory --timeout=300s

# Verify cluster health
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl cluster health
```

### 4. Deploy Workers

```bash
# Build worker image
docker build -t factory-worker:latest -f infra/workers/Dockerfile .

# Tag and push to your registry
docker tag factory-worker:latest your-registry.io/factory-worker:latest
docker push your-registry.io/factory-worker:latest

# Update image in deployment
# Edit infra/workers/base/deployment.yaml to use your image

# Create worker secrets
kubectl create secret generic factory-secrets \
  --from-literal=anthropic-api-key="your-key-here" \
  --namespace=software-factory

# Deploy workers
kubectl apply -k infra/workers/base
```

### 5. Verify Everything Works

```bash
# Check all pods
kubectl get pods -n software-factory

# Check worker logs
kubectl logs -l app=factory-worker -n software-factory

# Verify workers are polling
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl task-queue describe --task-queue factory-tasks
```

## Next Steps

- Read [docs/ops/TEMPORAL_OPERATIONS.md](../../docs/ops/TEMPORAL_OPERATIONS.md) for detailed operations
- Configure monitoring (Prometheus, Grafana)
- Set up backups
- Tune resources for your workload

## Troubleshooting

**Pods not starting?**
```bash
kubectl describe pod <pod-name> -n software-factory
kubectl logs <pod-name> -n software-factory
```

**Can't connect to Temporal?**
```bash
kubectl get svc -n software-factory
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl cluster health
```

**Database issues?**
```bash
kubectl logs postgres-0 -n software-factory
kubectl exec -it postgres-0 -n software-factory -- \
  psql -U temporal -c "SELECT 1;"
```

For more help, see the full operations guide.
