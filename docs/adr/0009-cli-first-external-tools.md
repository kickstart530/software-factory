# ADR-0009: CLI-first external tools (Phase 2b refinement)

- **Status:** Accepted
- **Date:** 2026-11-02
- **Supersedes:** Parts of ADR-0006 for Phase 2b implementation

## Context

ADR-0006 proposed an MCP-based tool gateway for all external actions. However, Roy's constraint for Phase 2b is: **NO MCP**, including no MCP tool gateway. We need external tool integration that works with CLI-first tooling while maintaining the same security, audit, and credential-injection properties.

## Decision

For Phase 2b, external tools are invoked through **CLI executables** with a standard contract:

1. **Tool definition format**: Tools are declared in YAML with:
   - `name`, `description`, `parameters` (JSON Schema)
   - `command`: template string with `{param}` substitutions
   - `requires_approval`: boolean for high-risk operations
   - `credentials`: list of environment variables to inject

2. **Tool invocation flow**:
   ```
   Agent → Harness tool router → Policy check → [Approval request if needed] → CLI exec
                                       ↓                                          ↓
                                  Audit log                              stdout/stderr/exit
   ```

3. **Credential injection**: Credentials are environment variables, never passed as CLI args or present in model context. The harness injects them before exec.

4. **Audit trail**: Every tool call (args, policy decision, approval if needed, output, exit code) is logged.

5. **Approval hook**: High-risk tools pause execution and write an approval request to `.factory-runs/{run_id}/pending-approval.json`. The CLI operator uses `factory approve` to review and approve/deny.

## Consequences

**Positive:**
+ No MCP server infrastructure needed for Phase 2b
+ Tools are shell scripts or executables—easy to write and test
+ Credentials flow through environment variables (standard practice)
+ Clear approval boundary for human review

**Negative:**
- No reusable MCP server ecosystem (that's the trade-off Roy accepted)
- Each tool needs a CLI wrapper if calling an API
- Less standardized than MCP protocol

**Migration path:**
ADR-0006's MCP gateway remains the long-term vision. Phase 2b's CLI-first approach is compatible: a future MCP gateway can proxy to these same CLI tools, or the tools can be ported to MCP servers later.

## Implementation

- `factory/harness/cli_tools.py`: CLI tool loader and executor
- `config/tools/`: YAML tool definitions (e.g. `github.yaml`, `slack.yaml`)
- `factory/harness/approvals.py`: Approval request/response handling
- CLI: `factory approve list|show|accept|deny`
