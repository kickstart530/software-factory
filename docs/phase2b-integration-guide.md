# Phase 2b Integration Guide: Policy Engine & Human Approvals

This guide explains how to use the Phase 2b policy engine and human approval system.

## Overview

Phase 2b adds:
- **Enhanced policy engine** with OPA support
- **Human approval service** (CLI-based)
- **Risk-based controls** for high-risk operations
- **Separation of concerns** between rule-based and approval-based gates

## Architecture

```
Agent → Harness → EnhancedPolicy → [RulePolicy] → [OPA Policy] → [Approval Check]
                       ↓                ↓              ↓              ↓
                    Allowed        Hard Deny    Conditional    Requires Human
                                                    Deny
```

## Policy Evaluation Flow

1. **RulePolicy** (hard denials):
   - Force-push, destructive commands
   - Privilege escalation
   - Protected path access without sufficient risk tier
   - Taint-based blocks (untrusted content)

2. **OPA Policy** (if configured):
   - Custom organizational policies in Rego
   - Can deny operations or mark them as requiring approval
   - Evaluated via `opa eval` CLI

3. **Approval Requirements**:
   - Configured tools always require approval
   - OPA can mark operations as approval-required
   - Approval requests pause task execution

## Configuration

### 1. Policy Files

Create OPA policies in `config/policies/*.rego`:

```rego
package factory

# Example: CI/CD changes require approval
allow := {
    "allow": true,
    "requires_approval": true,
    "approval_reason": "CI/CD workflow changes require approval",
    "rule": "cicd-approval"
} if {
    input.tool_name in ["write_file", "edit_file"]
    regex.match(`^\.github/workflows/`, input.rel_path)
}
```

### 2. Harness Configuration

In your harness setup:

```python
from pathlib import Path
from factory.harness.policy_engine import EnhancedPolicy, OPAPolicy
from factory.harness.policy import RulePolicy
from factory.harness.approvals import FileBasedApprovalService

# Create policy
rule_policy = RulePolicy()
opa_policy = OPAPolicy(
    policy_dir=Path("config/policies"),
    entrypoint="data.factory.allow"
)
approval_required_tools = {"deploy", "delete_database", "migrate"}

policy = EnhancedPolicy(
    rule_policy=rule_policy,
    opa_policy=opa_policy,
    approval_required_tools=approval_required_tools
)

# Create approval service
approval_service = FileBasedApprovalService(
    approvals_dir=Path(".factory-runs/approvals")
)
```

### 3. Tool Definition (CLI-first)

Tools are declared in YAML (see `config/tools/README.md`):

```yaml
name: deploy_production
description: Deploy to production environment
category: deployment

parameters:
  type: object
  properties:
    service:
      type: string
    version:
      type: string
  required: [service, version]

command: |
  ./scripts/deploy.sh "{service}" "{version}"

requires_approval: true
sensitive: false
mutates: true

credentials:
  - DEPLOY_TOKEN
```

## Using the Approval CLI

### List Pending Approvals

```bash
factory approve list --state-dir .factory-runs
```

Output:
```
================================================================================
ID:           req-001
Task:         task-implement-feature
Work Order:   wo-123
Agent:        implementer
Tool:         run_command
Requested:    2026-11-02T01:23:45Z
Expires:      2026-11-03T01:23:45Z

Reason:       deployment requires approval
Risk factors: deployment, production

Tool args:
{
  "command": "kubectl apply -f deploy/production.yaml"
}
================================================================================

1 pending request(s)

To review: factory approve show <request-id>
To approve: factory approve accept <request-id> --reason '...'
To deny: factory approve deny <request-id> --reason '...'
```

### Review a Specific Request

```bash
factory approve show req-001 --state-dir .factory-runs
```

### Approve a Request

```bash
factory approve accept req-001 \
  --reason "Reviewed and verified. Change is low-risk." \
  --user "alice@example.com" \
  --state-dir .factory-runs
```

### Deny a Request

```bash
factory approve deny req-001 \
  --reason "Deployment window is closed. Re-request during maintenance window." \
  --user "bob@example.com" \
  --state-dir .factory-runs
```

## Approval Request Flow

1. **Agent makes tool call** that requires approval
2. **Harness creates ApprovalRequest**:
   - Written to `.factory-runs/approvals/pending/{request-id}.json`
   - Task execution pauses
3. **Human reviews via CLI** (`factory approve list/show`)
4. **Human decides** (`factory approve accept/deny`)
   - Decision written to `.factory-runs/approvals/decisions/{request-id}.json`
   - Request moved to `.factory-runs/approvals/decided/`
5. **Harness resumes** task with approval decision

## OPA Policy Development

### Input Schema

OPA policies receive this input:

```json
{
  "agent": "implementer",
  "tool_name": "write_file",
  "tool_args": {"path": ".github/workflows/deploy.yml", "content": "..."},
  "risk_tier": "LOW",
  "work_order_id": "wo-123",
  "task_id": "task-456",
  "tainted": false,
  "rel_path": ".github/workflows/deploy.yml"
}
```

### Output Schema

OPA policies return:

```json
{
  "allow": true,
  "reason": "optional explanation",
  "rule": "rule-identifier",
  "requires_approval": false,
  "approval_reason": "why approval is needed"
}
```

### Testing OPA Policies

```bash
# Test a policy with sample input
opa eval -d config/policies -I input.json --format pretty data.factory.allow

# Example input.json:
{
  "agent": "implementer",
  "tool_name": "write_file",
  "tool_args": {"path": ".github/workflows/deploy.yml"},
  "risk_tier": "LOW",
  "tainted": false,
  "rel_path": ".github/workflows/deploy.yml"
}
```

## Risk Tiers

Work Orders have risk tiers that affect policy:

- **LOW**: Standard changes, minimal review
- **MEDIUM**: Moderate impact, additional scrutiny
- **HIGH**: High-impact changes (auth, payments, infra, migrations)

Protected paths require minimum risk tiers:

```python
DEFAULT_PROTECTED_PATHS = {
    RiskTier.HIGH: (
        r"^\.github/workflows/",
        r"(^|/)Dockerfile$",
        r"^infra/",
        r"(^|/)migrations/",
    ),
}
```

## Monitoring and Audit

All approval requests and decisions are logged:

```bash
# View approval history
ls .factory-runs/approvals/decided/

# View request details
cat .factory-runs/approvals/decided/req-001.json
```

Policy decisions are recorded in task telemetry:

```python
# In harness traces (.factory-runs/traces/*.jsonl)
{
  "event": "policy_check",
  "tool": "run_command",
  "decision": {
    "allowed": true,
    "requires_approval": true,
    "approval_reason": "deployment requires approval"
  }
}
```

## Best Practices

1. **Start with RulePolicy**: Use hard denials for dangerous operations
2. **Add OPA for complexity**: When organizational policies need logic
3. **Mark tools explicitly**: Set `requires_approval: true` in tool definitions
4. **Test policies**: Use `opa test` with fixtures before deploying
5. **Set reasonable timeouts**: Default is 24h; adjust via `expires_at`
6. **Monitor denials**: Review policy denials to tune rules
7. **Document approval reasons**: Clear reasons help human reviewers

## Future Work (Post Phase 2b)

- Slack/Teams integration for approval notifications
- Web UI for approval management
- Multi-approver workflows (N-of-M)
- Policy simulation mode (dry-run)
- Approval delegation rules
- Time-based approval windows

## Troubleshooting

### Approval request not appearing

Check:
```bash
ls -la .factory-runs/approvals/pending/
```

If empty, verify policy configuration and tool marking.

### OPA evaluation failing

```bash
# Test OPA directly
opa eval -d config/policies --format pretty data.factory.allow

# Check OPA is installed
which opa
opa version
```

### Approval timeout

Requests expire after 24 hours by default. Increase in code:

```python
request.expires_at = (datetime.now(UTC) + timedelta(hours=48)).isoformat()
```

## Example: End-to-End Workflow

1. Task attempts to deploy to production:
   ```python
   tool_call = {
       "name": "run_command",
       "input": {"command": "kubectl apply -f production.yaml"}
   }
   ```

2. Policy engine evaluates:
   - RulePolicy: ✓ allowed
   - OPA: ✓ allowed, but requires_approval=true
   - Result: **Approval required**

3. Harness creates request:
   ```
   .factory-runs/approvals/pending/req-001.json
   ```

4. Human reviews:
   ```bash
   factory approve list
   factory approve show req-001
   ```

5. Human approves:
   ```bash
   factory approve accept req-001 --reason "Change reviewed, LGTM"
   ```

6. Harness reads decision and proceeds with deployment

7. Audit trail preserved:
   - `.factory-runs/approvals/decided/req-001.json`
   - `.factory-runs/approvals/decisions/req-001.json`
