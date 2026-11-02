# Evaluation Harness

The software-factory evaluation system provides offline testing of prompt, model, and harness changes before they reach production.

## Components

### 1. Golden Tasks (`evals/golden/`)

Golden tasks are recorded task executions with expected outcomes. They serve as regression tests for the agent harness.

**Current golden set** (5 tasks):
- `fix_add_function.json`: Fix a subtract bug in add() function
- `budget_exceeded.json`: Task that exceeds turn budget
- `escalation.json`: Task requiring human escalation
- `write_new_module.json`: Create a new file
- `refactor_with_tests.json`: Extract code to new module

### 2. Replay Runner (`evals/replay.py`)

Re-executes golden tasks with:
- Model responses replayed from recordings
- Full harness logic (policy, hooks, gates, budgets)
- Isolated workspace per task
- Deterministic, fast execution

**Usage:**
```bash
factory evals replay
factory evals replay --json results.json
```

### 3. Eval Gate (`evals/gate.py`)

Compares baseline vs candidate configurations to detect regressions before merge.

**Usage:**
```bash
factory evals gate \
  --baseline-agents agents/ \
  --baseline-models config/models.yaml \
  --candidate-agents agents-new/ \
  --candidate-models config/models-new.yaml \
  --json gate-report.json
```

**Exit codes:**
- `0`: No regressions (gate passes)
- `1`: Regressions detected (gate blocks)

## Quick Start

### Run All Golden Tasks

```bash
factory evals replay
```

### Gate a Prompt Change

```bash
# Backup current config
cp -r agents/ agents-baseline/

# Make your changes to agents/

# Run the gate
factory evals gate \
  --baseline-agents agents-baseline/ \
  --candidate-agents agents/
```

### Gate a Model Change

```bash
# Backup current config
cp config/models.yaml config/models-baseline.yaml

# Make your changes to config/models.yaml

# Run the gate
factory evals gate \
  --baseline-models config/models-baseline.yaml \
  --candidate-models config/models.yaml
```

## CI Integration

The eval gate is designed for CI:

```yaml
- name: Run eval gate
  run: |
    factory evals gate \
      --baseline-agents baseline-agents/ \
      --candidate-agents agents/ \
      --json gate-report.json
```

See `.github/workflows/eval-gate-example.yml` for a complete workflow.

## Documentation

- **[EVALS.md](../docs/EVALS.md)**: Eval harness, golden tasks, replay
- **[EVAL_GATING.md](../docs/EVAL_GATING.md)**: Eval gate usage, CI integration, best practices

## Adding Golden Tasks

To add a new golden task:

1. Run a real task and capture the transcript
2. Extract task definition, agent spec, model responses
3. Define expected outcome (status, gates)
4. Save as `evals/golden/<name>.json`
5. Run `factory evals replay` to verify

Golden tasks should be:
- **Deterministic**: Same inputs → same outputs
- **Focused**: Test one scenario per task
- **Minimal**: As few turns as needed
- **Documented**: Clear description of what's tested

## Test Coverage

Run tests:
```bash
pytest tests/test_evals_replay.py -v
pytest tests/test_eval_gate.py -v
pytest tests/test_eval_gate_cli.py -v
```

## Phase 5: Continuous Improvement

This eval system implements Phase 5 of the factory roadmap:
> Eval-gated prompt/model updates, ensuring changes don't introduce regressions

**Status**: ✅ Complete
- CLI-first mechanism (no MCP)
- Baseline vs candidate comparison
- Gate report with regressions
- Non-zero exit on regression
- CI integration examples
- Comprehensive tests and docs
