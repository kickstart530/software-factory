# Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-durable-workflow-orchestration.md) | Durable workflow engine orchestrates; the LLM does not | Accepted |
| [0002](0002-ephemeral-sandbox-per-task.md) | One task runs in one ephemeral sandbox | Accepted |
| [0003](0003-typed-artifact-coordination.md) | Agents coordinate through typed artifacts, not chat | Accepted |
| [0004](0004-git-source-of-truth.md) | Git is the source of truth for code | Accepted |
| [0005](0005-model-gateway.md) | Models are reached through a gateway; model IDs live only in config | Accepted |
| [0006](0006-tool-gateway-mcp.md) | All external actions go through a tool gateway (MCP) | Accepted |
| [0007](0007-policy-as-code.md) | Policy-as-code decides what each agent may do | Accepted |
| [0008](0008-deterministic-verification-gates.md) | Deterministic verification before LLM judgement | Accepted |

New decisions: copy the structure of an existing ADR, take the next number, and link it here.
