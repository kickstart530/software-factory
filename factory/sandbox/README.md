# Sandbox Pod Spawning (Phase 2a)

Ephemeral Kubernetes pods for isolated agent task execution (AD-2: one task = one sandbox).

## Architecture

```
Temporal Activity (run_task_activity)
    │
    ├─► SandboxSpawner.spawn_and_wait()
    │       │
    │       ├─► Creates Pod manifest (clean HOME, security context)
    │       ├─► Spawns Pod in factory-sandboxes namespace
    │       ├─► Polls for completion (with heartbeats)
    │       ├─► Fetches logs
    │       └─► Deletes Pod (cleanup)
    │
    └─► Returns TaskResult
```

## Security Model

### Credential Isolation

**Problem:** Local `run_command` currently keeps host HOME/PATH and can read:
- `~/.netrc` (HTTP basic auth)
- `~/.config/gh` (GitHub CLI tokens)
- `~/.ssh` (SSH keys)
- `~/.aws` (AWS credentials)
- Any host environment variables

**Solution:** Sandbox pods have:
- ✅ **Clean HOME**: `/home/sandbox` (empty directory, no host mounts)
- ✅ **Minimal PATH**: `/usr/local/bin:/usr/bin:/bin` (no host paths)
- ✅ **No credential mounts**: No volumes from `~/.ssh`, `~/.config`, etc.
- ✅ **Explicit injection**: Credentials via Secrets Broker (Phase 2b)
- ✅ **Non-root execution**: `runAsUser: 1000`, `allowPrivilegeEscalation: false`

### Network Isolation

**Problem:** Local execution has no egress restrictions.

**Solution:** NetworkPolicy + optional egress proxy:
- ✅ **Default deny-all**: No egress unless explicitly allowed
- ✅ **Allowlist mode**: Allow specific CIDRs or domains
- ✅ **DNS allowed**: Pods can resolve names (but not connect without allowlist)
- ⚠️ **Domain filtering requires proxy**: NetworkPolicy only supports CIDRs, not FQDNs

### Runtime Isolation

- ✅ **gVisor runtime class**: User-space kernel for syscall filtering
- ✅ **Resource quotas**: CPU, memory, ephemeral storage limits
- ✅ **Seccomp profile**: `RuntimeDefault` seccomp restrictions
- ✅ **Capability drop**: `capabilities.drop: [ALL]`

## Configuration

### Environment Variables

```bash
# Kubernetes namespace for sandbox pods
export FACTORY_SANDBOX_NAMESPACE=factory-sandboxes

# Runtime class (gvisor, kata, or none for default)
export FACTORY_SANDBOX_RUNTIME_CLASS=gvisor

# Container image
export FACTORY_SANDBOX_IMAGE=gcr.io/your-project/agent-harness:latest

# Egress policy mode (deny-all, allowlist, allow-all)
export FACTORY_SANDBOX_EGRESS_MODE=allowlist
```

### Python API

```python
from factory.sandbox import SandboxConfig, SandboxSpawner, EgressPolicy
from factory.schemas import Task

# Custom configuration
config = SandboxConfig(
    namespace="factory-sandboxes",
    runtime_class="gvisor",
    egress=EgressPolicy(
        mode="allowlist",
        allowed_domains=[
            "pypi.org",
            "github.com",
            "api.anthropic.com",
        ],
    ),
)

spawner = SandboxSpawner(config)

# Spawn sandbox for a task
task = Task(...)
result = await spawner.spawn_and_wait(task)
```

### Temporal Worker Integration

```python
from temporalio.client import Client
from factory.temporal_supervisor import create_temporal_worker, TemporalConfig
from factory.sandbox import run_task_in_sandbox


async def main():
    config = TemporalConfig(host="temporal.example.com:7233")
    client = await config.create_client()

    # Worker with sandbox spawning
    worker = await create_temporal_worker(
        client,
        task_queue="factory-tasks",
        runner=run_task_in_sandbox,  # Async runner that spawns sandboxes
    )

    await worker.run()
```

## Egress Control

### NetworkPolicy (IP-based)

For CIDR-based allowlists:

```python
config = SandboxConfig(
    egress=EgressPolicy(
        mode="allowlist",
        allowed_cidrs=[
            "10.0.0.0/8",  # Internal services
            "151.101.0.0/16",  # GitHub
        ],
    ),
)
```

Generates a `NetworkPolicy` that blocks all egress except allowed CIDRs + DNS.

### Egress Proxy (Domain-based)

For domain-based allowlists, deploy an egress proxy (Squid, Envoy):

1. **Deploy egress proxy** in the cluster
2. **Configure proxy** with domain allowlist:
   ```python
   from factory.sandbox.network_policy import create_egress_proxy_config
   
   proxy_config = create_egress_proxy_config(egress_policy)
   # Apply to your proxy deployment
   ```
3. **Set HTTP_PROXY** in sandbox pods:
   ```python
   # In spawner.py _create_pod_manifest():
   env_vars.append({"name": "HTTP_PROXY", "value": "http://egress-proxy.factory-system:3128"})
   ```

### Production Recommendations

- **Phase 2a pilot**: Use `deny-all` or simple CIDR allowlist
- **Phase 2b**: Deploy egress proxy for domain-based filtering
- **Phase 3**: Integrate with service mesh (Istio, Linkerd) for advanced policies

## Credential Injection (Phase 2b)

Credentials will be injected via:
1. **Secrets Broker** (Vault): mints short-lived, task-scoped tokens
2. **Kubernetes Secrets**: mounted as files (not env vars)
3. **Workload Identity**: OIDC federation for cloud provider credentials

Example (future):

```python
# In spawner.py _create_pod_manifest():
volume_mounts.append(
    {
        "name": "git-credentials",
        "mountPath": "/secrets/git",
        "readOnly": True,
    }
)

volumes.append(
    {
        "name": "git-credentials",
        "secret": {
            "secretName": f"task-{task.id}-git-creds",
        },
    }
)

env_vars.append(
    {
        "name": "GIT_ASKPASS",
        "value": "/secrets/git/askpass.sh",
    }
)
```

## Deployment Prerequisites

### 0. Agent-harness image and API key

Sandbox pods run `factory sandbox-entry` from the agent-harness image: it clones
the task's repository (`Task.repo`), checks out the task branch, runs the harness
and prints the TaskResult on a `FACTORY_TASK_RESULT ` line, which the spawner
reads back from the pod log. A pod that exits without printing one is a failure.

```bash
docker build -f sandbox/agent-harness.Dockerfile -t software-factory/agent-harness:latest .
kubectl -n factory-sandboxes create secret generic factory-secrets \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY"
```

Override the Secret name and key with `SandboxConfig.api_key_secret` /
`api_key_secret_key`. Only public repositories can be cloned until the Secrets
Broker supplies clone tokens.

### 1. Kubernetes Namespace

```bash
kubectl create namespace factory-sandboxes
```

### 2. Service Account

```bash
kubectl create serviceaccount factory-sandbox-runner -n factory-sandboxes
```

### 3. gVisor Runtime Class

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
```

GKE:
```bash
gcloud container node-pools create gvisor-pool \
  --cluster=factory-cluster \
  --machine-type=n2-standard-4 \
  --sandbox type=gvisor
```

### 4. NetworkPolicy

Apply the generated NetworkPolicy:

```python
from factory.sandbox.network_policy import create_network_policy
from factory.sandbox import SandboxConfig

config = SandboxConfig.from_env()
policy = create_network_policy(config.namespace, config.egress)

# Apply to cluster
# kubectl apply -f policy.yaml
```

## Testing

### Unit Tests

```bash
pytest tests/test_sandbox.py -v
```

### Integration Tests (requires cluster)

```bash
# Set kubeconfig
export KUBECONFIG=~/.kube/config

# Run integration tests
pytest tests/test_sandbox_integration.py -v --integration
```

### Manual Test

```python
import asyncio
from factory.sandbox import SandboxSpawner
from factory.schemas import Task


async def test():
    task = Task(
        id="test-001",
        work_order_id="wo-001",
        agent="implementer",
        title="Test sandbox",
        instructions="Run a simple command",
    )

    spawner = SandboxSpawner()
    result = await spawner.spawn_and_wait(task)
    print(result)


asyncio.run(test())
```

## Monitoring

Sandbox pods emit:
- **Logs**: Captured and returned in `SandboxResult.logs`
- **Duration**: `SandboxResult.duration_seconds`
- **Exit code**: `SandboxResult.exit_code`
- **Pod events**: Check with `kubectl get events -n factory-sandboxes`

## Troubleshooting

### Pod stuck in Pending

```bash
kubectl describe pod <pod-name> -n factory-sandboxes
```

Common causes:
- No nodes with gVisor runtime class
- Insufficient resources
- Image pull failure

### Pod killed by OOM

Increase memory limits in `SandboxConfig.resources.memory_limit`.

### Network timeout

Check NetworkPolicy:
```bash
kubectl get networkpolicy -n factory-sandboxes
```

Verify egress rules allow the destination.

### Permission denied errors

Check security context and volume permissions. Ensure `runAsUser: 1000` matches the image's user.

## Future Work (not in Phase 2a)

- [ ] Checkpoint/resume support (serialize workspace to object storage)
- [ ] Secrets Broker integration (Vault)
- [ ] Egress proxy deployment (Squid/Envoy)
- [ ] Service mesh integration (Istio)
- [ ] Prebuilt image caching (registry mirror)
- [ ] Multi-region pod placement
- [ ] Cost tracking per task
