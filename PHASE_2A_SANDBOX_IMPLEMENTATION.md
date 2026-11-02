# Phase 2a: Sandbox Pod Spawning Implementation

This branch implements **sandbox pod spawning** for the TaskRunner path in Temporal activities, addressing Phase 2a requirements for ephemeral, isolated agent execution (AD-2: one task = one sandbox).

## What's Implemented

### 1. Sandbox Pod Spawner (`factory/sandbox/`)

**New modules:**
- `config.py`: Configuration for sandbox pods (image, egress, resources, runtime class)
- `spawner.py`: `SandboxSpawner` class that creates Kubernetes pods and waits for completion
- `network_policy.py`: NetworkPolicy generation for egress control
- `README.md`: Comprehensive documentation on security model and deployment

**Key features:**
- ✅ **Credential isolation**: Clean HOME (`/home/sandbox`), no host mounts of `~/.ssh`, `~/.config`, etc.
- ✅ **Network isolation**: NetworkPolicy with deny-all default, allowlist support
- ✅ **Runtime isolation**: gVisor/Kata runtime class, security context, resource quotas
- ✅ **Non-root execution**: `runAsUser: 1000`, `allowPrivilegeEscalation: false`
- ✅ **Minimal environment**: Clean PATH, no host env var pollution
- ✅ **Async execution**: Spawns pod, polls with heartbeats, fetches logs, cleans up

### 2. Temporal Supervisor Updates (`factory/temporal_supervisor.py`)

**Fixed TaskRunner injection:**
- ❌ **Before**: Looked for runner in `heartbeat_details` (always empty)
- ✅ **After**: Uses `ContextVar` registry, populated by worker at startup
- `set_task_runner()`: Worker calls this to inject the runner
- `create_temporal_worker()`: Now properly injects `run_task_in_sandbox` or custom runner

**Contract support:**
- Sync or async runners supported
- Async runners receive `heartbeat_callback` kwarg for long-running tasks
- Maintains same `TaskRunner` contract as in-process Supervisor

### 3. Security Model

**Problem (local execution):**
- Host HOME accessible → `~/.netrc`, `~/.config/gh`, AWS/SSH credentials readable
- No egress restrictions → unrestricted network access
- Host PATH → can access host tools and configs

**Solution (sandbox pods):**
- Clean HOME: `/home/sandbox` (empty emptyDir volume)
- No host mounts: `~/.ssh`, `~/.config`, `~/.aws` never mounted
- Explicit credential injection: Via Secrets Broker (Phase 2b, documented)
- Egress control: NetworkPolicy deny-all by default, CIDR/domain allowlist
- Minimal PATH: `/usr/local/bin:/usr/bin:/bin` only
- Non-root: `runAsUser: 1000`, capability drop `ALL`
- gVisor isolation: User-space kernel, syscall filtering

### 4. Kubernetes Manifests

**Reference manifests:**
- `k8s-manifests.yaml`: Namespace, ServiceAccount, NetworkPolicy, RuntimeClass, quotas
- `worker-deployment.yaml`: Temporal worker deployment with RBAC for pod management

**Deployment prerequisites documented:**
- Namespace creation
- gVisor runtime class setup (GKE/EKS instructions)
- RBAC for workers to spawn pods
- NetworkPolicy for egress control
- Optional egress proxy for domain-based filtering

### 5. Tests (`tests/test_sandbox.py`)

**Unit tests (19 tests, all passing):**
- Configuration loading (env vars, defaults)
- Pod name sanitization (DNS-1123 compliance)
- Pod manifest generation (security context, volumes, env vars)
- NetworkPolicy generation (deny-all, allowlist, CIDRs)
- Egress proxy config generation

**Integration tests (marked `@pytest.mark.integration`, skipped without cluster):**
- Real pod spawning and waiting
- Exit code handling
- Log collection

## What's NOT Included (Out of Scope)

Per task instructions, this PR does NOT touch:
- ❌ GitHub App integration (other agents)
- ❌ Temporal production cluster IaC (minimal, thin interface only)
- ❌ Worker deployment automation (reference manifests only)
- ❌ Phase 2b policy/approvals
- ❌ docs/GETTING_STARTED.md updates

Still to do in Phase 2a (other PRs):
- Checkpoint streaming via activity heartbeats
- Secrets Broker integration (Phase 2b dependency)
- Full Temporal integration tests
- Production Temporal cluster setup

## Testing

### Run unit tests:
```bash
pytest tests/test_sandbox.py -v -m "not integration"
pytest tests/test_temporal_supervisor.py -v
```

All unit tests pass (26 tests total, 1 skipped integration test in temporal_supervisor).

### Run integration tests (requires Kubernetes cluster):
```bash
export KUBECONFIG=/path/to/kubeconfig
pytest tests/test_sandbox.py -v --integration
```

## Usage

### Temporal Worker with Sandbox Spawning

```python
from temporalio.client import Client
from factory.temporal_supervisor import create_temporal_worker, TemporalConfig
from factory.sandbox import run_task_in_sandbox


async def main():
    config = TemporalConfig(host="temporal.example.com:7233")
    client = await config.create_client()

    # Worker with sandbox spawning (async runner)
    worker = await create_temporal_worker(
        client,
        task_queue="factory-tasks",
        runner=run_task_in_sandbox,
    )
    await worker.run()
```

### Configuration

Via environment variables:
```bash
export FACTORY_SANDBOX_NAMESPACE=factory-sandboxes
export FACTORY_SANDBOX_RUNTIME_CLASS=gvisor
export FACTORY_SANDBOX_IMAGE=gcr.io/proj/harness:latest
export FACTORY_SANDBOX_EGRESS_MODE=deny-all
```

Or via Python:
```python
from factory.sandbox import SandboxConfig, EgressPolicy

config = SandboxConfig(
    namespace="factory-sandboxes",
    runtime_class="gvisor",
    egress=EgressPolicy(
        mode="allowlist",
        allowed_domains=["github.com", "pypi.org"],
    ),
)
```

## Dependencies

Added to `pyproject.toml`:
- `kubernetes>=31.0`: Kubernetes Python client for pod management

## Documentation

- `factory/sandbox/README.md`: Comprehensive security model, deployment guide, troubleshooting
- `docs/TEMPORAL_INTEGRATION.md`: Updated with sandbox architecture reference
- `factory/sandbox/k8s-manifests.yaml`: Reference Kubernetes manifests
- `factory/sandbox/worker-deployment.yaml`: Reference worker deployment with RBAC

## Constraints Met

✅ **CLI-first**: No MCP, no tool gateway dependencies  
✅ **Thin interface**: Minimal coupling to Temporal, no cluster IaC ownership  
✅ **One task = one sandbox**: Each `run_task_activity` spawns ephemeral pod  
✅ **Credential isolation**: Clean HOME, no host mounts, explicit injection  
✅ **Egress control**: NetworkPolicy + optional proxy, deny-all default  
✅ **Reviewable**: Focused PR, no extraneous changes  

## Security Highlights

🔒 **No host credentials accessible** (HOME=/home/sandbox, no ~/.ssh mounts)  
🔒 **Network deny-all by default** (allowlist via NetworkPolicy)  
🔒 **Non-root execution** (runAsUser: 1000, no privilege escalation)  
🔒 **gVisor isolation** (user-space kernel, syscall filtering)  
🔒 **Resource quotas** (CPU, memory, ephemeral storage limits)  
🔒 **Seccomp + capability drop** (RuntimeDefault profile, drop ALL capabilities)
