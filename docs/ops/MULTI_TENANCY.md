# Multi-Tenancy Guide

The software factory supports multi-tenancy with isolated budgets, policies, and quotas per tenant.

## Overview

Multi-tenancy in the factory provides:

1. **Budget Isolation**: Each tenant has separate cost tracking and limits
2. **Policy Namespace**: Per-tenant policy configurations
3. **Quota Enforcement**: Prevent tenants from exceeding allocated resources
4. **Cost Allocation**: Detailed per-tenant billing and reporting

## Tenant Identification

Tenants are identified by a `tenant_id` string on work orders and tasks:

```python
work_order = WorkOrder(
    id="WO-123",
    tenant_id="acme-corp",
    title="Add payment webhook",
    # ... other fields
)
```

The `tenant_id`:
- Propagates from work orders to all their tasks
- Defaults to `"default"` if not specified
- Used for budget ledger keys and policy lookups
- Appears in all telemetry and audit logs

## Budget Configuration

Set per-tenant budget limits:

```python
from factory.control.budget_ledger import FileBudgetLedger, TenantBudgetConfig

ledger = FileBudgetLedger(Path(".factory-runs/budgets"))

config = TenantBudgetConfig(
    tenant_id="acme-corp",
    monthly_usd_limit=500.0,  # $500/month
    daily_usd_limit=50.0,  # $50/day
    tokens_per_task_limit=100000,  # 100K tokens per task
    enabled=True,
)

ledger.set_tenant_config(config)
```

Budget limits are checked:
- Before scheduling new tasks
- Periodically during task execution
- When recording usage to the ledger

## Usage Tracking

The budget ledger tracks usage per tenant per period:

```python
from factory.schemas import Usage

# Record usage for a completed task
usage = Usage(
    input_tokens=5000, output_tokens=1500, usd=0.25, turns=10, tool_calls=5, wall_clock_s=120.0
)

ledger.record_usage(tenant_id="acme-corp", usage=usage, task_id="T-456")
```

Usage is aggregated by:
- **Monthly**: `2024-03` format for billing cycles
- **Daily**: `2024-03-15` format for daily quotas

## Integration with Task Execution

The harness should check quotas before starting tasks:

```python
from factory.control.budget_ledger import FileBudgetLedger

ledger = FileBudgetLedger(Path(".factory-runs/budgets"))

# Before starting a task
tenant_id = task.tenant_id or "default"
estimated_usage = Usage(usd=10.0)  # Conservative estimate

allowed, reason = ledger.check_quota(tenant_id, estimated_usage)
if not allowed:
    # Pause tenant or escalate
    return TaskResult(task_id=task.id, status=TaskStatus.FAILED, error=f"Quota exceeded: {reason}")

# After task completes
ledger.record_usage(tenant_id, actual_usage, task.id)
```

## Cost Reporting

View costs per tenant:

```bash
# All tenants, current month
factory cost report

# Specific tenant
factory cost report --tenant acme-corp

# Specific period
factory cost report --tenant acme-corp --period 2024-03
```

Example output:

```
======================================================================
Cost Summary: 2024-03
Tenant: acme-corp
======================================================================
Total Cost:          $347.50
Total Tokens:        1,234,567
  Input:             987,654
  Output:            123,456
  Cache Read:        98,765
  Cache Write:       24,692
Turns:               234
Tool Calls:          567
Wall Clock Hours:    12.45
======================================================================
```

## Cost Export for Grafana/Prometheus

Export metrics to JSONL for ingestion by observability tools:

```bash
factory cost export \
  --output /var/metrics/factory-costs-2024-03.jsonl \
  --period 2024-03
```

Each line is a JSON record:

```json
{
  "period": "2024-03",
  "tenant_id": "acme-corp",
  "total_usd": 347.50,
  "total_tokens": 1234567,
  "input_tokens": 987654,
  "output_tokens": 123456,
  "cache_read_tokens": 98765,
  "cache_write_tokens": 24692,
  "turns": 234,
  "tool_calls": 567,
  "wall_clock_hours": 12.45
}
```

Ingest with Grafana Loki, Prometheus, or your observability stack.

## Policy Namespacing

Per-tenant policies in OPA (Phase 2b integration):

```rego
# policies/tenants/acme-corp/agent_tools.rego

package factory.acme_corp

import future.keywords.if

# acme-corp can deploy to their staging environment
allow if {
    input.agent == "release"
    input.tool_name == "kubectl_apply"
    input.tool_args.namespace == "acme-corp-staging"
}

# But not to production (requires human approval)
deny if {
    input.agent == "release"
    input.tool_name == "kubectl_apply"
    input.tool_args.namespace == "acme-corp-prod"
}
```

## State Storage Layout

Multi-tenant state is organized by tenant:

```
.factory-runs/
├── budgets/
│   ├── configs/
│   │   ├── acme-corp.json
│   │   └── widgets-inc.json
│   └── usage/
│       ├── acme-corp/
│       │   ├── 2024-03.json       # Monthly aggregate
│       │   ├── 2024-03-15.json    # Daily aggregate
│       │   └── 2024-03-16.json
│       └── widgets-inc/
│           └── 2024-03.json
├── kill_switch/
│   └── tenants/
│       ├── acme-corp.json
│       └── widgets-inc.json
└── checkpoints/
    ├── WO-acme-123/
    │   └── T-456/
    └── WO-widgets-789/
        └── T-101/
```

## Production Deployment

For production multi-tenancy:

1. **Database partitioning**: Use tenant_id as partition key in Postgres
2. **Namespace isolation**: Kubernetes namespaces per tenant or tenant group
3. **Secret isolation**: Per-tenant Vault namespaces or AWS IAM roles
4. **Network isolation**: VPC/subnet per tenant for high-security deployments
5. **Audit logging**: Separate audit logs per tenant for compliance

## Billing Integration

Integrate with billing systems:

```python
# Example: monthly billing job
from factory.control.budget_ledger import FileBudgetLedger
from factory.control.cost_dashboard import CostDashboard

ledger = FileBudgetLedger(Path(".factory-runs/budgets"))
dashboard = CostDashboard(ledger)

period = "2024-03"
summaries = dashboard.get_all_tenants_summary(period)

for summary in summaries:
    # Send to billing system
    billing_api.create_invoice(
        tenant_id=summary.tenant_id,
        period=period,
        amount_usd=summary.total_usd,
        line_items=[
            {
                "description": f"AI Factory Usage - {period}",
                "quantity": summary.total_tokens,
                "unit": "tokens",
                "amount": summary.total_usd,
            }
        ],
    )
```

## Best Practices

1. **Always set tenant_id**: Even for single-tenant deployments (use "default")
2. **Conservative limits**: Start with low quotas and increase based on usage
3. **Daily quotas**: Prevent runaway costs with daily limits
4. **Cost alerts**: Alert both factory ops and tenant admins on quota warnings
5. **Regular reviews**: Audit tenant usage monthly for capacity planning
6. **Soft limits**: Warn tenants at 80% of quota before hard stopping
7. **Graduated pricing**: Different tiers can have different per-token costs
