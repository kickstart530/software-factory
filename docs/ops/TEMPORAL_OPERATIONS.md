# Temporal Operations Guide

Comprehensive guide for operators deploying and managing the Software Factory Temporal cluster and workers.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Deploying the Temporal Cluster](#deploying-the-temporal-cluster)
4. [Deploying Workers](#deploying-workers)
5. [Verification and Health Checks](#verification-and-health-checks)
6. [Common Operations](#common-operations)
7. [Monitoring and Observability](#monitoring-and-observability)
8. [Troubleshooting](#troubleshooting)
9. [Backup and Recovery](#backup-and-recovery)
10. [Scaling](#scaling)
11. [Upgrading](#upgrading)

---

## Prerequisites

### Required Tools

```bash
# Kubernetes CLI
kubectl version --client

# Kustomize (v4.0+)
kustomize version

# Temporal CLI (for admin operations)
brew install temporal
# or
go install go.temporal.io/sdk/cmd/temporal@latest

# PostgreSQL client (for database operations)
psql --version

# Optional: k9s for interactive cluster management
brew install k9s
```

### Cluster Requirements

- **Kubernetes**: 1.25+
- **Storage Class**: Dynamic provisioning for PostgreSQL PVCs
- **Resources**: Minimum 16 CPU cores, 32Gi RAM available
- **Networking**: ClusterIP service support, CoreDNS functional
- **RBAC**: Enabled with appropriate permissions for the operator

### Access Verification

```bash
# Verify cluster access
kubectl cluster-info
kubectl get nodes

# Verify namespace creation permissions
kubectl auth can-i create namespaces

# Verify storage classes
kubectl get storageclass
```

---

## Initial Setup

### 1. Generate PostgreSQL Password

```bash
# Generate a strong random password
export POSTGRES_PASSWORD=$(openssl rand -base64 32)

# Store it securely (e.g., in a password manager or secret store)
echo "PostgreSQL Password: $POSTGRES_PASSWORD"

# Create the secret (do not commit this file!)
cat > /tmp/postgres-secret.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: temporal-postgres-secret
  namespace: software-factory
type: Opaque
stringData:
  password: $POSTGRES_PASSWORD
EOF

# Apply the secret
kubectl create namespace software-factory --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f /tmp/postgres-secret.yaml
rm /tmp/postgres-secret.yaml
```

### 2. Prepare Infrastructure Manifests

```bash
# Clone the repository
git clone https://github.com/kickstart530/software-factory.git
cd software-factory

# Verify manifests
kustomize build infra/temporal/base > /tmp/temporal-base.yaml
kustomize build infra/temporal/overlays/production > /tmp/temporal-production.yaml

# Review the generated manifests
less /tmp/temporal-production.yaml
```

### 3. Customize for Your Environment

Edit production overlays as needed:

```bash
# Example: Change storage class
cat > infra/temporal/overlays/production/storage-class-patch.yaml <<EOF
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  volumeClaimTemplates:
    - metadata:
        name: postgres-data
      spec:
        storageClassName: fast-ssd  # Your storage class
EOF

# Add to kustomization.yaml
# patches:
#   - path: storage-class-patch.yaml
```

---

## Deploying the Temporal Cluster

### Development Deployment

For testing and development:

```bash
# Deploy base configuration
kubectl apply -k infra/temporal/base

# Wait for pods to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n software-factory --timeout=300s
kubectl wait --for=condition=ready pod -l app=temporal -n software-factory --timeout=300s
```

### Production Deployment

For production environments:

```bash
# Deploy production configuration
kubectl apply -k infra/temporal/overlays/production

# Monitor deployment
watch kubectl get pods -n software-factory

# Check rollout status
kubectl rollout status deployment/temporal-frontend -n software-factory
kubectl rollout status deployment/temporal-history -n software-factory
kubectl rollout status deployment/temporal-matching -n software-factory
kubectl rollout status deployment/temporal-worker -n software-factory
kubectl rollout status statefulset/postgres -n software-factory
```

### Post-Deployment Configuration

```bash
# Exec into admin tools pod
kubectl exec -it temporal-admin-tools -n software-factory -- /bin/bash

# Register the default namespace (if not auto-created)
tctl --namespace default namespace register

# Verify cluster health
tctl cluster health

# List registered namespaces
tctl namespace list

# Exit the admin tools pod
exit
```

---

## Deploying Workers

### Build Worker Image

Workers need the factory Python application packaged:

```bash
# From repository root
cd software-factory

# Build the worker image
docker build -t factory-worker:latest -f infra/workers/Dockerfile .

# Tag for your registry
docker tag factory-worker:latest your-registry.io/factory-worker:v1.0.0

# Push to registry
docker push your-registry.io/factory-worker:v1.0.0
```

**Dockerfile** (create at `infra/workers/Dockerfile`):

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy application
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[temporal]"

# Create non-root user
RUN useradd -m -u 1000 factory && chown -R factory:factory /app
USER factory

# Health check endpoint
EXPOSE 8080 9090

CMD ["python", "-m", "factory.worker"]
```

### Deploy Workers

```bash
# Update image reference in deployment
# Edit infra/workers/base/deployment.yaml or create an overlay

# Development
kubectl apply -k infra/workers/base

# Production
kubectl apply -k infra/workers/overlays/production

# Verify workers are running
kubectl get pods -l app=factory-worker -n software-factory
kubectl logs -l app=factory-worker -n software-factory --tail=50 -f
```

### Create Factory Secrets

```bash
# Create secrets for worker operations
kubectl create secret generic factory-secrets \
  --from-literal=anthropic-api-key="YOUR_ANTHROPIC_KEY" \
  --namespace=software-factory

# Verify secret
kubectl get secret factory-secrets -n software-factory
```

---

## Verification and Health Checks

### Cluster Health

```bash
# Check all pods
kubectl get pods -n software-factory -o wide

# Check services
kubectl get svc -n software-factory

# Temporal cluster health
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl cluster health

# Database connectivity
kubectl exec -it postgres-0 -n software-factory -- \
  psql -U temporal -c "SELECT 1;"
```

### Worker Health

```bash
# Check worker pods
kubectl get pods -l app=factory-worker -n software-factory

# Check worker logs for connection
kubectl logs -l app=factory-worker -n software-factory | grep "Connected to Temporal"

# Verify workers are polling
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl task-queue describe --task-queue factory-tasks
```

### Metrics Verification

```bash
# Port-forward to Temporal frontend metrics
kubectl port-forward -n software-factory svc/temporal-frontend 9090:9090

# In another terminal, query metrics
curl http://localhost:9090/metrics | grep temporal_

# Port-forward to worker metrics
kubectl port-forward -n software-factory svc/factory-worker 9091:9090

# Query worker metrics
curl http://localhost:9091/metrics | grep factory_
```

---

## Common Operations

### Managing Namespaces

```bash
# Register a new namespace
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl --namespace my-namespace namespace register \
  --retention 7 \
  --description "My namespace"

# Update namespace retention
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl --namespace my-namespace namespace update \
  --retention 14

# List all namespaces
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl namespace list
```

### Workflow Operations

```bash
# List workflows
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow list

# Describe a workflow
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow describe --workflow-id <ID>

# Show workflow history
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow show --workflow-id <ID>

# Terminate a stuck workflow
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow terminate --workflow-id <ID> --reason "Manual termination"

# Cancel a workflow
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow cancel --workflow-id <ID>
```

### Task Queue Management

```bash
# Describe a task queue
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl task-queue describe --task-queue factory-tasks

# List pollers on a task queue
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl task-queue list-pollers --task-queue factory-tasks
```

### Restart Components

```bash
# Restart Temporal frontend
kubectl rollout restart deployment/temporal-frontend -n software-factory

# Restart all Temporal services
kubectl rollout restart deployment -l app=temporal -n software-factory

# Restart workers
kubectl rollout restart deployment/factory-worker -n software-factory

# Restart PostgreSQL (careful - causes brief downtime)
kubectl rollout restart statefulset/postgres -n software-factory
```

---

## Monitoring and Observability

### Logs

```bash
# Temporal frontend logs
kubectl logs -l component=frontend -n software-factory -f --tail=100

# Temporal history logs
kubectl logs -l component=history -n software-factory -f --tail=100

# Worker logs
kubectl logs -l app=factory-worker -n software-factory -f --tail=100

# PostgreSQL logs
kubectl logs postgres-0 -n software-factory -f --tail=100

# All logs from the namespace
kubectl logs -n software-factory --all-containers=true --tail=50 -f
```

### Prometheus Integration

Create a ServiceMonitor for Prometheus Operator:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: temporal-metrics
  namespace: software-factory
spec:
  selector:
    matchLabels:
      app: temporal
  endpoints:
    - port: metrics
      interval: 30s
      path: /metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: factory-worker-metrics
  namespace: software-factory
spec:
  selector:
    matchLabels:
      app: factory-worker
  endpoints:
    - port: metrics
      interval: 30s
      path: /metrics
```

### Grafana Dashboards

Import community dashboards:
- [Temporal Server Dashboard](https://github.com/temporalio/dashboards)
- Custom worker metrics dashboard (create based on exposed metrics)

### Key Metrics to Monitor

**Temporal Server:**
- `temporal_request_latency` - API request latencies
- `temporal_workflow_success` / `temporal_workflow_failed` - Workflow outcomes
- `temporal_activity_execution_latency` - Activity execution times
- `temporal_persistence_latency` - Database query latencies

**Workers:**
- `factory_task_duration_seconds` - Task execution times
- `factory_task_success_total` / `factory_task_failed_total` - Task outcomes
- `factory_worker_active_tasks` - Current task load
- `factory_sandbox_spawn_duration_seconds` - Sandbox creation time

**PostgreSQL:**
- Connection pool usage
- Query latency
- Disk I/O
- Database size

---

## Troubleshooting

### Workers Not Connecting to Temporal

**Symptoms:**
- Workers log "Failed to connect to Temporal"
- No pollers shown in `tctl task-queue describe`

**Diagnosis:**
```bash
# Check frontend service
kubectl get svc temporal-frontend -n software-factory

# Test connectivity from worker pod
kubectl exec -it <worker-pod> -n software-factory -- \
  nc -zv temporal-frontend.software-factory.svc.cluster.local 7233

# Check DNS resolution
kubectl exec -it <worker-pod> -n software-factory -- \
  nslookup temporal-frontend.software-factory.svc.cluster.local
```

**Solutions:**
- Verify service exists and has endpoints
- Check network policies aren't blocking traffic
- Ensure DNS is functional in the cluster
- Verify TEMPORAL_ADDRESS environment variable is correct

### PostgreSQL Connection Issues

**Symptoms:**
- Temporal services crashing with "connection refused"
- Init containers stuck waiting for database

**Diagnosis:**
```bash
# Check PostgreSQL pod
kubectl get pod postgres-0 -n software-factory

# Check PostgreSQL logs
kubectl logs postgres-0 -n software-factory | tail -50

# Verify secret exists
kubectl get secret temporal-postgres-secret -n software-factory

# Test database connection
kubectl exec -it postgres-0 -n software-factory -- \
  psql -U temporal -c "SELECT version();"
```

**Solutions:**
- Verify secret has correct password
- Ensure PVC is bound and has space
- Check PostgreSQL container logs for startup errors
- Verify resource limits aren't causing OOM kills

### Workflow Execution Failures

**Symptoms:**
- Workflows stuck in running state
- Activities timing out
- Workers not picking up tasks

**Diagnosis:**
```bash
# Check workflow status
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow describe --workflow-id <ID>

# Check task queue
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl task-queue describe --task-queue factory-tasks

# Check worker logs
kubectl logs -l app=factory-worker -n software-factory | grep ERROR
```

**Solutions:**
- Verify workers are healthy and have capacity
- Check worker logs for panics or errors
- Increase worker concurrency if queue is backing up
- Verify activities aren't exceeding timeout values

### High Memory Usage / OOM Kills

**Symptoms:**
- Pods restarting with OOMKilled status
- Performance degradation

**Diagnosis:**
```bash
# Check pod resource usage
kubectl top pods -n software-factory

# Check pod events
kubectl describe pod <pod-name> -n software-factory | grep -A 10 Events

# Review resource requests/limits
kubectl get pod <pod-name> -n software-factory -o jsonpath='{.spec.containers[*].resources}'
```

**Solutions:**
- Increase memory limits for affected components
- Scale horizontally instead of vertically where possible
- Check for memory leaks in worker code
- Tune PostgreSQL memory settings

### Slow Performance

**Symptoms:**
- High workflow latency
- Workers taking long to pick up tasks
- Database queries slow

**Diagnosis:**
```bash
# Check metrics
kubectl port-forward -n software-factory svc/temporal-frontend 9090:9090
curl http://localhost:9090/metrics | grep latency

# Check database performance
kubectl exec -it postgres-0 -n software-factory -- \
  psql -U temporal -c "SELECT * FROM pg_stat_activity;"

# Check history service shard distribution
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl admin shard describe
```

**Solutions:**
- Scale up history service replicas
- Increase PostgreSQL resources
- Add database indexes if missing
- Check for network latency between pods
- Review and optimize slow workflows

---

## Backup and Recovery

### Database Backup

**Automated Backups:**

```bash
# Create a CronJob for daily backups
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
  namespace: software-factory
spec:
  schedule: "0 2 * * *"  # 2 AM daily
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: postgres:15-alpine
              env:
                - name: PGPASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: temporal-postgres-secret
                      key: password
              command:
                - /bin/sh
                - -c
                - |
                  TIMESTAMP=\$(date +%Y%m%d_%H%M%S)
                  pg_dump -h postgres.software-factory.svc.cluster.local -U temporal temporal > /backup/temporal_\${TIMESTAMP}.sql
                  pg_dump -h postgres.software-factory.svc.cluster.local -U temporal temporal_visibility > /backup/temporal_visibility_\${TIMESTAMP}.sql
                  # Upload to object storage (configure as needed)
              volumeMounts:
                - name: backup
                  mountPath: /backup
          restartPolicy: OnFailure
          volumes:
            - name: backup
              persistentVolumeClaim:
                claimName: postgres-backup-pvc
EOF
```

**Manual Backup:**

```bash
# Backup all databases
kubectl exec -it postgres-0 -n software-factory -- \
  pg_dump -U temporal temporal | gzip > temporal_backup_$(date +%Y%m%d).sql.gz

kubectl exec -it postgres-0 -n software-factory -- \
  pg_dump -U temporal temporal_visibility | gzip > temporal_visibility_backup_$(date +%Y%m%d).sql.gz

# Copy backups locally
kubectl cp software-factory/postgres-0:/path/to/backup.sql.gz ./backup.sql.gz
```

### Restore from Backup

```bash
# Stop Temporal services
kubectl scale deployment --all --replicas=0 -n software-factory -l app=temporal

# Restore database
gunzip -c temporal_backup.sql.gz | \
  kubectl exec -i postgres-0 -n software-factory -- \
  psql -U temporal temporal

gunzip -c temporal_visibility_backup.sql.gz | \
  kubectl exec -i postgres-0 -n software-factory -- \
  psql -U temporal temporal_visibility

# Restart Temporal services
kubectl scale deployment --all --replicas=1 -n software-factory -l app=temporal
kubectl scale deployment temporal-frontend --replicas=3 -n software-factory
kubectl scale deployment temporal-history --replicas=5 -n software-factory
```

### Disaster Recovery Plan

1. **Document Configuration**: Keep `kustomization.yaml` and overlays in version control
2. **Secret Backup**: Store secrets in a secure vault (e.g., HashiCorp Vault, AWS Secrets Manager)
3. **Database Backups**: Automated daily backups with point-in-time recovery capability
4. **Recovery Time Objective (RTO)**: 1 hour
5. **Recovery Point Objective (RPO)**: 24 hours (from daily backups)

**Recovery Procedure:**

```bash
# 1. Provision new cluster
kubectl cluster-info

# 2. Restore secrets
kubectl create secret generic temporal-postgres-secret \
  --from-literal=password="$POSTGRES_PASSWORD" \
  --namespace=software-factory

# 3. Deploy infrastructure
kubectl apply -k infra/temporal/overlays/production

# 4. Wait for PostgreSQL
kubectl wait --for=condition=ready pod -l app=postgres -n software-factory --timeout=300s

# 5. Restore database from backup
# (see "Restore from Backup" above)

# 6. Deploy Temporal and workers
kubectl apply -k infra/temporal/overlays/production
kubectl apply -k infra/workers/overlays/production

# 7. Verify
kubectl exec -it temporal-admin-tools -n software-factory -- tctl cluster health
```

---

## Scaling

### Horizontal Scaling

**Temporal Services:**

```bash
# Scale frontend for more API capacity
kubectl scale deployment temporal-frontend --replicas=5 -n software-factory

# Scale history for more workflow capacity
kubectl scale deployment temporal-history --replicas=10 -n software-factory

# Scale matching for more task queue capacity
kubectl scale deployment temporal-matching --replicas=5 -n software-factory
```

**Workers:**

```bash
# Manual scaling
kubectl scale deployment factory-worker --replicas=10 -n software-factory

# HPA is enabled by default; adjust thresholds
kubectl edit hpa factory-worker -n software-factory
```

### Vertical Scaling

Update resource limits in production overlay:

```yaml
# infra/temporal/overlays/production/patches/history-resources.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: temporal-history
spec:
  template:
    spec:
      containers:
        - name: temporal-history
          resources:
            requests:
              cpu: 2000m
              memory: 4Gi
            limits:
              cpu: 8000m
              memory: 8Gi
```

Apply changes:

```bash
kubectl apply -k infra/temporal/overlays/production
kubectl rollout status deployment/temporal-history -n software-factory
```

### PostgreSQL Scaling

**Vertical Scaling:**

```bash
# Update StatefulSet resources
kubectl edit statefulset postgres -n software-factory

# Restart PostgreSQL pod
kubectl rollout restart statefulset postgres -n software-factory
```

**Storage Expansion:**

```bash
# Expand PVC (if storage class supports it)
kubectl patch pvc postgres-data-postgres-0 -n software-factory \
  -p '{"spec":{"resources":{"requests":{"storage":"200Gi"}}}}'

# Verify expansion
kubectl get pvc postgres-data-postgres-0 -n software-factory
```

---

## Upgrading

### Temporal Server Upgrade

1. **Review Release Notes**: Check [Temporal Releases](https://github.com/temporalio/temporal/releases)
2. **Test in Non-Production**: Always test upgrades in dev/staging first
3. **Backup Database**: Create a full backup before upgrading

**Upgrade Procedure:**

```bash
# 1. Backup database
kubectl exec -it postgres-0 -n software-factory -- \
  pg_dump -U temporal temporal > temporal_pre_upgrade_backup.sql

# 2. Update image tags in manifests
# Edit infra/temporal/base/server.yaml
# Change: image: temporalio/server:1.22.4
# To:     image: temporalio/server:1.23.0

# 3. Apply updates
kubectl apply -k infra/temporal/overlays/production

# 4. Monitor rollout
kubectl rollout status deployment/temporal-frontend -n software-factory
kubectl rollout status deployment/temporal-history -n software-factory
kubectl rollout status deployment/temporal-matching -n software-factory
kubectl rollout status deployment/temporal-worker -n software-factory

# 5. Verify cluster health
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl cluster health

# 6. Check for any failed workflows
kubectl exec -it temporal-admin-tools -n software-factory -- \
  tctl workflow list --query 'ExecutionStatus="Failed"'
```

**Rollback:**

```bash
# Revert to previous image version
kubectl rollout undo deployment/temporal-frontend -n software-factory
kubectl rollout undo deployment/temporal-history -n software-factory
kubectl rollout undo deployment/temporal-matching -n software-factory
kubectl rollout undo deployment/temporal-worker -n software-factory
```

### Worker Upgrade

```bash
# 1. Build new worker image
docker build -t factory-worker:v1.1.0 -f infra/workers/Dockerfile .
docker push your-registry.io/factory-worker:v1.1.0

# 2. Update deployment image
kubectl set image deployment/factory-worker \
  worker=your-registry.io/factory-worker:v1.1.0 \
  -n software-factory

# 3. Monitor rollout (zero-downtime with RollingUpdate strategy)
kubectl rollout status deployment/factory-worker -n software-factory

# 4. Verify new workers are processing tasks
kubectl logs -l app=factory-worker -n software-factory --tail=20
```

### PostgreSQL Upgrade

PostgreSQL major version upgrades require special handling:

```bash
# Use pg_upgrade or create a new cluster and migrate
# This is beyond the scope of this guide; consult PostgreSQL documentation
```

---

## Appendix: Quick Reference

### Useful kubectl Commands

```bash
# View all resources
kubectl get all -n software-factory

# Describe a resource
kubectl describe <resource-type> <resource-name> -n software-factory

# Get pod logs
kubectl logs <pod-name> -n software-factory [-c <container-name>] [-f]

# Execute command in pod
kubectl exec -it <pod-name> -n software-factory -- <command>

# Port forward
kubectl port-forward -n software-factory <resource> <local-port>:<remote-port>

# View events
kubectl get events -n software-factory --sort-by='.lastTimestamp'

# Resource usage
kubectl top nodes
kubectl top pods -n software-factory
```

### Temporal tctl Commands

```bash
# All tctl commands run via admin-tools pod:
kubectl exec -it temporal-admin-tools -n software-factory -- tctl <command>

# Common commands:
tctl cluster health
tctl namespace list
tctl workflow list
tctl workflow describe --workflow-id <ID>
tctl workflow show --workflow-id <ID>
tctl workflow terminate --workflow-id <ID> --reason "<reason>"
tctl task-queue describe --task-queue <name>
```

### Emergency Contacts

- **Temporal Cloud Support**: support@temporal.io
- **Community Slack**: https://temporal.io/slack
- **Factory Team**: [Add your team's contact info]

---

## References

- [Temporal Documentation](https://docs.temporal.io/)
- [Temporal Self-Hosted Guide](https://docs.temporal.io/self-hosted-guide)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Software Factory Implementation Plan](../IMPLEMENTATION_PLAN.md)
- [ADR-0001: Durable Workflow Orchestration](../adr/0001-durable-workflow-orchestration.md)
