# Per-Repository Tuning (Phase 5)

This document describes the **per-repository configuration override** feature,
allowing individual repositories to customize agent behavior without forking
the software-factory.

## Overview

Different repositories have different needs:
- A small, well-tested library may run agents with tighter budgets to save cost.
- A high-risk production service may require stricter verification gates.
- A monorepo may need extended timeouts for large test suites.
- A team may want to standardize on specific model tiers.

The `.factory/repo.yaml` file allows each repository to override:
- **Agent budgets** (tokens, USD, wall-clock time, turns, tool calls)
- **Verification gates** (which checks must pass before task completion)
- **Model tiers** (frontier, mid, small)
- **Max gate attempts** (how many retries before escalation)
- **Conventions pointer** (link to repo-specific docs like `CONTRIBUTING.md`)

All fields are optional. Missing fields use the factory's global defaults from
`agents/*.yaml` and `config/models.yaml`.

## Configuration File

Place `.factory/repo.yaml` in the repository root alongside `.factory/gates.yaml`.

### Example

```yaml
# .factory/repo.yaml
description: "Tighter budgets for our small utility library"

# Point to repo-specific conventions document
conventions: "docs/CONTRIBUTING.md"

# Per-agent overrides
agents:
  # Reduce implementer budget for cost savings
  implementer:
    model_tier: mid  # downgrade from frontier to mid
    budget:
      tokens: 2000000  # half the default 4M
      usd: 12.50       # half the default $25
      wall_clock_s: 1800  # 30 minutes instead of 45
      turns: 100       # reduced from 150
      tool_calls: 300  # reduced from 400
    gates: [build, unit_tests, lint]  # drop secret_scan if handled elsewhere
    max_gate_attempts: 2  # fail faster

  # Ensure reviewer always uses frontier tier
  reviewer:
    model_tier: frontier
    gates: [build, unit_tests, lint, secret_scan, integration_tests]
    max_gate_attempts: 5

  # Only override model tier, keep other defaults
  planner:
    model_tier: frontier
```

### Schema Reference

```yaml
description: string (optional)
  # Human-readable description of these overrides

conventions: string (optional)
  # Path to repo conventions doc (relative to repo root)
  # e.g., "CONTRIBUTING.md" or "docs/AGENTS.md"

agents: dict (optional)
  # Per-agent overrides, keyed by agent name
  <agent_name>:
    model_tier: "frontier" | "mid" | "small" (optional)
      # Override the agent's model tier

    budget: (optional)
      # Override individual budget fields; omitted fields use defaults
      tokens: int (optional, >= 1)
      usd: float (optional, >= 0)
      wall_clock_s: int (optional, >= 1)
      turns: int (optional, >= 1)
      tool_calls: int (optional, >= 1)

    gates: list[string] (optional)
      # Override which verification gates must pass
      # Gates are defined in .factory/gates.yaml

    max_gate_attempts: int (optional, >= 1)
      # Override how many times gates may be retried
```

## CLI Commands

### Validate Config

Check that `.factory/repo.yaml` is valid:

```bash
factory repo-config validate --workspace /path/to/repo
```

Output:
- ✓ if valid, showing description, conventions, and overridden agents
- Reports if no config exists (defaults will be used)
- ✗ if invalid, with error details

### Show Effective Config

Show the effective configuration for an agent after applying repo overrides:

```bash
factory repo-config show --workspace /path/to/repo --agent implementer
```

Output shows:
- Source: `default`, `repo-override`, or `partial-override`
- Effective model tier, budget, gates, max attempts
- Conventions pointer if set
- Which fields were overridden and their before/after values

**JSON output:**

```bash
factory repo-config show --workspace /path/to/repo --agent implementer --json
```

Returns structured JSON with the effective configuration.

**Example output (text):**

```
Effective configuration for agent 'implementer' in /path/to/repo
================================================================================
Source:            repo-override
Model tier:        mid
Budget:
  tokens:          2,000,000
  usd:             $12.50
  wall_clock_s:    1,800s
  turns:           100
  tool_calls:      300
Gates:             build, unit_tests, lint
Max gate attempts: 2
Conventions:       docs/CONTRIBUTING.md

================================================================================
Overrides from .factory/repo.yaml:
  model_tier: frontier → mid
  budget overrides:
    tokens: 4,000,000 → 2,000,000
    usd: $25.00 → $12.50
    wall_clock_s: 2,700s → 1,800s
    turns: 150 → 100
    tool_calls: 400 → 300
  gates: ['build', 'unit_tests', 'lint', 'secret_scan'] → ['build', 'unit_tests', 'lint']
  max_gate_attempts: 3 → 2
```

## Integration with Harness

When `factory run` executes a task, the harness:

1. Loads the agent definition from `agents/<agent>.yaml`
2. Loads `.factory/repo.yaml` from the workspace (if present)
3. Applies repo overrides to the agent spec:
   - Budget fields: repo value if set, else agent default
   - Model tier: repo value if set, else agent default
   - Gates: repo list if set, else agent default
   - Max gate attempts: repo value if set, else agent default
4. Runs the task with the effective configuration

This happens transparently; no changes to task definitions or workflows are needed.

## Precedence

The precedence chain is:

1. **Repo override** (`.factory/repo.yaml` for this agent)
2. **Agent default** (`agents/<agent>.yaml`)

There is no intermediate layer. Global factory defaults are baked into the agent
definitions in `agents/*.yaml`.

## Use Cases

### Cost Optimization for Small Repos

A small utility library with 2K LOC and fast tests:

```yaml
description: "Cost-optimized for small codebase"
agents:
  implementer:
    model_tier: mid
    budget: {tokens: 1000000, usd: 5.0}
  reviewer:
    model_tier: mid
```

### Stricter Gates for High-Risk Repos

A production payment service:

```yaml
description: "High-stakes production service with strict verification"
agents:
  implementer:
    gates: [build, unit_tests, integration_tests, lint, secret_scan, sast]
    max_gate_attempts: 5
  reviewer:
    model_tier: frontier  # always use best model
    gates: [build, unit_tests, integration_tests]
```

### Extended Timeouts for Monorepos

A large monorepo with 15-minute test suites:

```yaml
description: "Extended timeouts for monorepo"
agents:
  implementer:
    budget:
      wall_clock_s: 5400  # 90 minutes
  integrator:
    budget:
      wall_clock_s: 3600  # 60 minutes
```

### Model Tier Standardization

A team that wants all agents on `mid` tier for cost control:

```yaml
description: "Standardize on mid tier for cost control"
agents:
  implementer: {model_tier: mid}
  reviewer: {model_tier: mid}
  planner: {model_tier: mid}
  integrator: {model_tier: mid}
```

## Testing

To test a config before deploying:

1. **Validate the syntax:**
   ```bash
   factory repo-config validate --workspace .
   ```

2. **Inspect effective config for each agent:**
   ```bash
   factory repo-config show --workspace . --agent implementer
   factory repo-config show --workspace . --agent reviewer
   ```

3. **Run a test task locally:**
   ```bash
   factory run --task test-task.json --workspace . --state-dir /tmp/test-run
   ```

The harness will log which overrides were applied at the start of the run.

## Conventions Pointer

The `conventions` field points to a file (relative to repo root) that documents
repo-specific coding standards, testing practices, and contribution guidelines.

When set, the harness includes this pointer in the agent's context so it can
reference the conventions when making decisions. This replaces the need to embed
repo-specific rules in every task description.

**Example:** If `conventions: "docs/AGENTS.md"`, agents will be told:
> "This repository's conventions are documented in docs/AGENTS.md at the workspace root."

## Limitations

1. **No recursive overrides:** You cannot override `soft_limit_ratio` or other
   harness-internal settings. Only agent-visible config can be overridden.

2. **No per-task overrides:** Overrides are per-agent across all tasks in the repo.
   If a specific task needs different settings, escalate or use a different agent.

3. **No environment-specific overrides:** The config is repo-wide. If dev/staging/prod
   environments need different settings, use separate repos or feature flags.

4. **Gates must be defined:** If you list a gate in `gates: [...]`, it must be
   defined in `.factory/gates.yaml` or be a built-in gate. Undefined gates fail closed.

## Best Practices

1. **Start with defaults:** Only override when you have measured need. The factory's
   defaults are tuned for general-purpose use.

2. **Document your overrides:** Use the `description` field to explain why these
   overrides exist, so future maintainers understand the rationale.

3. **Version control:** Commit `.factory/repo.yaml` to version control and review
   changes like code. Budgets and gates affect quality and cost.

4. **Monitor and adjust:** Track agent runs, costs, and failure rates. If an agent
   repeatedly hits budget limits or fails gates, adjust the overrides.

5. **Use conventions:** Prefer pointing to a `conventions` doc over duplicating
   guidelines in every task description.

## Migration from Global Config

If you previously forked the factory or used environment variables for per-repo
settings, migrate to `.factory/repo.yaml`:

1. Identify what was customized (budgets, tiers, gates).
2. Create `.factory/repo.yaml` with those overrides.
3. Validate and test (see above).
4. Remove the fork or environment variable workarounds.

The config surface is stable and versioned with the factory; future factory
upgrades will respect your repo overrides.

## Related

- [Verification Gates](../docs/adr/0008-deterministic-verification-gates.md)
- [Agent Definitions](../agents/)
- [Model Gateway](../docs/adr/0005-model-gateway.md)
- [Policy as Code](../docs/adr/0007-policy-as-code.md)
