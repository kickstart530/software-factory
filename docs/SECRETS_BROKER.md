# Secrets Broker

The Secrets Broker provides short-lived, task-scoped credential management for
the software factory. It ensures that agents can access external resources
(git hosts, cloud APIs, etc.) without ever exposing credentials in model-visible
tool results or transcripts.

## Core Principles

1. **No standing credentials**: Tokens are minted per task, scoped to one repo/branch
2. **Short-lived**: Credentials expire with the task or after a configurable TTL (max 24h)
3. **Never in model context**: Credentials are injected server-side for CLI tools only
4. **Redacted from transcripts**: Pre-storage hooks strip minted secrets from transcripts

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Agent Harness                                                   │
│   ├─ Tool execution needs credentials                          │
│   ├─ Calls SecretsBroker.mint(task_id, repo, tool, scopes)    │
│   │    └─> Returns Credential metadata (NO secret value)       │
│   └─ Calls SecretsBroker.inject(credential_id, env)           │
│        └─> Injects secret into environment for tool execution  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ SecretsBroker Interface                                         │
│   ├─ mint(request) -> Credential (metadata only)              │
│   ├─ inject(credential_id, env) -> env (with secret)          │
│   ├─ revoke(credential_id) -> bool                            │
│   ├─ list_credentials(...) -> list[Credential]                │
│   └─ cleanup_expired() -> int                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
┌───────────────────────────┐  ┌───────────────────────────┐
│ EnvFileBackend            │  │ VaultBackend              │
│ (local/test)              │  │ (production)              │
│                           │  │                           │
│ Reads from:               │  │ Integrates with:          │
│ - Environment vars        │  │ - HashiCorp Vault CLI     │
│ - secrets.json file       │  │ - Dynamic secret engines  │
│ - .env file               │  │ - Lease revocation        │
└───────────────────────────┘  └───────────────────────────┘
```

## Backends

### EnvFileBackend (Local/Test)

File-backed backend for local development and testing. NOT FOR PRODUCTION.

**Configuration:**
```python
from factory.secrets.backends import EnvFileBackend

backend = EnvFileBackend(
    state_dir=Path(".factory-runs/secrets"),
    secrets_file=Path("secrets.json"),  # optional
    env_file=Path(".env"),              # optional
)
```

**Secrets sources (in priority order):**
1. `secrets.json` file with structured mappings:
   ```json
   {
     "tools": {
       "gh": "ghp_...",
       "git": "ghp_...",
       "gitlab": "glpat-..."
     }
   }
   ```
2. `.env` file with KEY=VALUE pairs
3. Environment variables (`GITHUB_TOKEN`, `GITLAB_TOKEN`, etc.)

### VaultBackend (Production)

Integrates with HashiCorp Vault for production credential management.

**Configuration:**
```python
from factory.secrets.backends import VaultBackend

backend = VaultBackend(
    state_dir=Path(".factory-runs/secrets"),
    vault_path="github/token",  # Vault secret engine path
    vault_role="agent",         # Role to use for requests
)
```

**Prerequisites:**
- `vault` CLI installed and in PATH
- `VAULT_ADDR` environment variable set
- `VAULT_TOKEN` environment variable set (or other auth method)
- Vault policies allowing read on `{vault_path}/{vault_role}`

**Vault secret engines supported:**
- GitHub App tokens: `github/token/{role}`
- AWS credentials: `aws/creds/{role}`
- GCP tokens: `gcp/token/{roleset}`
- Database credentials: `database/creds/{role}`

See `factory/secrets/backends/vault.py` for implementation details and
adaptation instructions for different secret engines.

## CLI Usage

### Mint a credential

```bash
factory secrets mint \
  --task-id task-123 \
  --work-order-id wo-456 \
  --repo-url https://github.com/example/repo \
  --branch main \
  --tool-name gh \
  --scopes repo read:org \
  --ttl 3600 \
  --reason "Implementer needs GitHub access"
```

Output:
```
✓ Minted credential: cred_abc123def456789012345678
  Task: task-123
  Tool: gh
  Expires: 2026-11-02T05:37:42.123456+00:00
  Fingerprint: a1b2c3d4e5f67890

NOTE: Secret value injected into environment, never displayed.
```

### List credentials

```bash
# List all credentials
factory secrets list

# Filter by task
factory secrets list --task-id task-123

# Filter by status
factory secrets list --status active
factory secrets list --status expired
factory secrets list --status revoked
```

### Check credential status

```bash
factory secrets status cred_abc123def456789012345678
```

Output:
```json
{
  "credential_id": "cred_abc123def456789012345678",
  "task_id": "task-123",
  "work_order_id": "wo-456",
  "repo_url": "https://github.com/example/repo",
  "branch": "main",
  "tool_name": "gh",
  "scopes": ["repo", "read:org"],
  "status": "active",
  "minted_at": "2026-11-02T04:37:42.123456+00:00",
  "expires_at": "2026-11-02T05:37:42.123456+00:00",
  "revoked_at": null,
  "fingerprint": "a1b2c3d4e5f67890",
  "metadata": {
    "reason": "Implementer needs GitHub access"
  }
}
```

**NOTE:** The secret value is NEVER included in the output.

### Revoke a credential

```bash
factory secrets revoke cred_abc123def456789012345678
```

### Clean up expired credentials

```bash
factory secrets cleanup
```

## Programmatic Usage

### In the Agent Harness

```python
from factory.secrets import MintRequest
from factory.secrets.backends import EnvFileBackend

# Initialize backend
backend = EnvFileBackend(state_dir=Path(".factory-runs/secrets"))

# Mint a credential
request = MintRequest(
    task_id=task.id,
    work_order_id=task.work_order_id,
    repo_url=task.repo.url,
    branch=task.branch,
    tool_name="gh",
    scopes=["repo"],
    ttl_seconds=3600,
    reason="Task execution needs GitHub access",
)
credential = backend.mint(request)

# Store credential_id for later use
# NOTE: credential object does NOT contain the secret value

# Later, when executing a tool:
env = os.environ.copy()
env = backend.inject_credential(credential.credential_id, env)

# Now env contains GITHUB_TOKEN and FACTORY_CREDENTIAL_ID
# Execute tool with this environment
subprocess.run(["gh", "pr", "create", ...], env=env)

# After task completes, revoke credential
backend.revoke(credential.credential_id)
```

### In Tests

```python
from factory.secrets import MintRequest
from factory.secrets.backends import EnvFileBackend

# Create temporary backend for test
backend = EnvFileBackend(state_dir=tmp_path)

# Override secrets for test
backend._secrets_map["gh"] = "test_token_12345"

# Mint and use credential
request = MintRequest(
    task_id="test-task",
    work_order_id="test-wo",
    repo_url="https://github.com/test/repo",
    tool_name="gh",
)
credential = backend.mint(request)

# Verify secret is NOT in credential
assert "test_token" not in credential.model_dump_json()

# Inject for tool execution
env = backend.inject_credential(credential.credential_id, {})
assert env["GITHUB_TOKEN"] == "test_token_12345"
```

## Security Properties

### 1. Secrets Never in Model Context

The Secrets Broker ensures that secret values are NEVER included in any
data structure that could be sent to the model:

- `Credential` objects contain metadata only (task ID, tool, scopes, expiry)
- `list_credentials()` returns metadata only
- `get_credential()` returns metadata only

The ONLY method that accesses secret values is `inject_credential()`, which
injects them directly into an environment dict for tool execution.

### 2. Redaction from Transcripts

Secret patterns are redacted from transcripts before storage:

```python
from factory.harness.hooks import redact_secrets

output = "Token: ghp_abc123..."
redacted = redact_secrets(output)
# Result: "Token: [REDACTED:github-token]"
```

Patterns include:
- AWS access keys
- Private keys
- GitHub tokens
- Anthropic API keys
- Slack tokens
- **Minted credential IDs** (`cred_*`)

### 3. Write Guards

The `secret_write_guard` hook prevents agents from writing secrets to files:

```python
# Agent tries to write a file containing a credential ID
tool = Tool(name="write_file", ...)
args = {"path": "config.yaml", "content": "token: cred_abc123..."}

decision = secret_write_guard(tool, args)
# decision.allowed = False
# decision.reason = "refusing to write what looks like a secret (minted-credential-id)"
```

### 4. Task Scoping

Every credential is scoped to exactly one task:

- Credentials include `task_id` and `work_order_id`
- Can optionally be restricted to a specific `branch`
- Credentials from one task cannot be used by another

### 5. Short TTL

Credentials are short-lived by design:

- Default TTL: 1 hour (3600 seconds)
- Maximum TTL: 24 hours (86400 seconds)
- Expired credentials are automatically rejected
- Credentials can be explicitly revoked before expiry

## Testing Strategy

The test suite (`tests/test_secrets_broker.py`) proves:

1. **Credentials can be minted** with correct metadata
2. **Secret values are NEVER exposed** in Credential objects
3. **Credentials expire correctly** and cannot be used after expiry
4. **Credentials can be revoked** before natural expiry
5. **Secrets are redacted** from text via hooks
6. **Both backends work correctly** (EnvFile and Vault)
7. **End-to-end: model context never receives secrets**

Run tests:
```bash
pytest tests/test_secrets_broker.py -v
```

## Future Enhancements

1. **Credential rotation**: Auto-rotate long-running task credentials
2. **Audit trail**: Detailed logs of credential minting, injection, and revocation
3. **Rate limiting**: Limit credential minting per task/work order
4. **Additional backends**: AWS Secrets Manager, GCP Secret Manager, Azure Key Vault
5. **Scope enforcement**: Verify that tools only request the scopes they need
6. **Anomaly detection**: Alert on unusual credential access patterns

## References

- Implementation Plan: `docs/IMPLEMENTATION_PLAN.md` (Phase 3, Hardening)
- ADR-0009: CLI-first external tools (`docs/adr/0009-cli-first-external-tools.md`)
- Hooks: `factory/harness/hooks.py`
- CLI: `factory/cli.py`
