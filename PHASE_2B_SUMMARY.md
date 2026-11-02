# Phase 2b: Policy Engine & Human Approvals

**Branch:** `cursor/phase-2b-policy-approvals-eb49`

## What This Adds

Phase 2b implements the policy engine and human approval system with a **CLI-first approach** (no MCP per Roy's constraint).

### Core Components

1. **Enhanced Policy Engine** (`factory/harness/policy_engine.py`)
   - Extends RulePolicy with OPA support
   - Policy evaluation via `opa eval` CLI
   - Approval requirements marking
   - Three-tier evaluation: RulePolicy → OPA → Approval checks

2. **Human Approval Service** (`factory/harness/approvals.py`)
   - File-based approval workflow
   - CLI-driven decision making
   - Request lifecycle: pending → approved/denied/expired
   - 24-hour default expiry

3. **CLI Commands** (updated `factory/cli.py`)
   ```bash
   factory approve list         # List pending requests
   factory approve show <id>    # Show request details
   factory approve accept <id>  # Approve a request
   factory approve deny <id>    # Deny a request
   ```

4. **OPA Policy Examples** (`config/policies/`)
   - `factory.rego`: Example factory policies
   - `risk.rego`: Risk tier rules
   - Rego-based policy definitions

5. **Updated Schemas** (`factory/schemas.py`)
   - `ApprovalRequest` / `ApprovalDecision`
   - `PolicyEvaluationInput` / `PolicyEvaluationResult`
   - Full contract for policy and approval systems

### Documentation

- **ADR-0009**: CLI-first external tools (supersedes MCP for Phase 2b)
- **Integration Guide**: `docs/phase2b-integration-guide.md`
- **Implementation Plan**: Updated to split Phase 2 into 2a/2b

### Tests

All new functionality tested:
- `tests/test_approvals.py` (7 tests, all passing)
- `tests/test_policy_engine.py` (6 tests, all passing)

## Design Decisions

### 1. CLI-First, No MCP

Per Roy's constraint, external tools are invoked via CLI executables rather than MCP servers. Future migration to MCP remains possible (see ADR-0009).

### 2. File-Based Approvals

For Phase 2b, approvals use filesystem-based coordination:
- Simple, no service infrastructure needed
- Easy to inspect/debug
- Can migrate to API-based service later

### 3. Three-Tier Policy

Policy evaluation is layered:
1. **RulePolicy** (hard denials) - Always evaluated first
2. **OPA Policy** (conditional/approval) - If configured
3. **Approval Requirements** (tool-level) - Final gate

This allows different control levels for different risk scenarios.

### 4. Backwards Compatible

The existing `RulePolicy` continues to work standalone. Enhanced policy is opt-in via configuration.

## What Works

✅ Policy engine evaluates rules via RulePolicy and OPA  
✅ Approval requests created and stored  
✅ CLI commands list, review, and decide requests  
✅ Schemas exported and validated  
✅ All Phase 2b tests passing  
✅ Agent definitions validate  
✅ Documentation complete

## What's Next (Future Phases)

- [ ] Integrate with harness loop to pause on approval-required
- [ ] Slack/Teams approval notifications
- [ ] Web UI for approval management
- [ ] Multi-approver workflows
- [ ] Approval delegation rules
- [ ] CLI tool executor (for external tool YAML definitions)
- [ ] MCP gateway (when MCP constraint is lifted)

## Files Changed

**Modified:**
- `docs/IMPLEMENTATION_PLAN.md` - Split Phase 2 into 2a/2b
- `factory/cli.py` - Added approval commands
- `factory/schemas.py` - Added approval and policy schemas

**New:**
- `docs/adr/0009-cli-first-external-tools.md`
- `docs/phase2b-integration-guide.md`
- `factory/harness/approvals.py`
- `factory/harness/policy_engine.py`
- `config/policies/factory.rego`
- `config/policies/risk.rego`
- `config/tools/README.md`
- `tests/test_approvals.py`
- `tests/test_policy_engine.py`
- `schemas/*.json` (4 new schema exports)

## Usage Example

```bash
# An agent attempts a high-risk operation
# Policy engine marks it as requiring approval
# Harness creates approval request

# Human reviews pending approvals
factory approve list

# Human inspects the request
factory approve show req-001

# Human approves or denies
factory approve accept req-001 --reason "Reviewed, LGTM" --user alice
# OR
factory approve deny req-001 --reason "Not during maintenance window" --user bob

# Harness resumes task execution with decision
```

## Testing

```bash
# Run Phase 2b tests
pytest tests/test_approvals.py tests/test_policy_engine.py -v

# Validate agent definitions
factory agents validate

# Export schemas
factory schemas export
```
