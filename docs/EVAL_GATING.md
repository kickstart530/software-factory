# Eval Gating: Testing Prompt and Model Changes

Eval gating runs the golden eval suite against a baseline and a candidate configuration **before merging**, and blocks on regressions. Because the suite replays recorded model responses, prompt and model changes are reported as inconclusive (and block) until golden tasks are re-recorded with them; see [What Gets Gated](#what-gets-gated).

## Overview

The eval gate compares two configurations:
- **Baseline**: The current production configuration
- **Candidate**: Your proposed changes

It runs the golden eval suite against both and reports:
- Pass rate changes
- Specific regressions (tasks that passed before but fail now)
- Improvements (tasks that failed before but pass now)
- Cost differences

## Usage

### Basic Usage

Compare current configuration (baseline) with proposed changes (candidate):

```bash
factory evals gate \
  --baseline-agents agents/ \
  --baseline-models config/models.yaml \
  --candidate-agents agents-proposed/ \
  --candidate-models config/models-proposed.yaml
```

### Testing Only Prompt Changes

If you're only changing prompts (in `agents/*.yaml` or `prompts/`):

```bash
factory evals gate \
  --baseline-agents agents/ \
  --candidate-agents agents-new-prompts/
```

### Testing Only Model Changes

If you're only changing model routing (in `config/models.yaml`):

```bash
factory evals gate \
  --baseline-models config/models.yaml \
  --candidate-models config/models-new-routing.yaml
```

### Save Detailed Report

Save a JSON report with full details:

```bash
factory evals gate \
  --baseline-agents agents/ \
  --candidate-agents agents-new/ \
  --json gate-report.json
```

## Exit Codes

The command exits with:
- **0** if no regressions are detected (gate passes)
- **1** if regressions are detected (gate blocks)

This makes it suitable for use in CI pipelines.

## Output

### Console Output

```
======================================================================
Running eval gate: baseline vs candidate
======================================================================

Running baseline (baseline)...
  4/5 passed

Running candidate (candidate)...
  3/5 passed

======================================================================
Eval Gate Report
======================================================================

Baseline:
  Passed: 4
  Failed: 1
  Pass rate: 80.0%
  Total cost: $0.0000

Candidate:
  Passed: 3
  Failed: 2
  Pass rate: 60.0%
  Total cost: $0.0000

❌ REGRESSIONS (1):
  - golden_001: Fix add() function (baseline: succeeded, candidate: failed)

======================================================================
❌ GATE BLOCKED: Regressions detected
======================================================================
```

### JSON Report

The JSON report includes:

```json
{
  "baseline": {
    "passed": 4,
    "failed": 1,
    "pass_rate": 0.8,
    "total_cost_usd": 0.0
  },
  "candidate": {
    "passed": 3,
    "failed": 2,
    "pass_rate": 0.6,
    "total_cost_usd": 0.0
  },
  "regressions": [
    "golden_001: Fix add() function (baseline: succeeded, candidate: failed)"
  ],
  "improvements": [],
  "has_regressions": true,
  "baseline_results": [ ... ],
  "candidate_results": [ ... ]
}
```

## CI Integration

### GitHub Actions

Add eval gating to your PR workflow:

```yaml
name: CI

on: [pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install -e .[dev]
      
      - name: Run eval gate
        run: |
          factory evals gate \
            --baseline-agents agents/ \
            --candidate-agents agents/ \
            --json gate-report.json
      
      - name: Upload gate report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: eval-gate-report
          path: gate-report.json
```

### Advanced Example with PR Comments

See `.github/workflows/eval-gate-example.yml` for a complete workflow that:
- Fetches baseline config from the main branch
- Compares baseline vs candidate
- Posts gate results as a PR comment
- Blocks merge on regressions
- Uploads detailed JSON report

This workflow only runs when agents, models, or prompts change.

### Local Development Workflow

Before submitting a PR with prompt or model changes:

1. **Make your changes** to agents/ or config/models.yaml
2. **Run the gate locally**:
   ```bash
   factory evals gate \
     --baseline-agents agents-backup/ \
     --candidate-agents agents/
   ```
3. **Review the report** and fix any regressions
4. **Commit and push** only when the gate passes

## Important Notes

### Replay Limitation

The current implementation uses **replay** mode, which means:
- Model responses are replayed from recorded golden tasks
- Prompt changes don't affect the model responses during replay
- The gate validates that harness behavior is consistent

To fully test prompt changes that should alter model behavior:
1. Record new golden tasks with the new prompt
2. Add them to the suite
3. Run the gate

This is by design: golden tasks test deterministic harness behavior. For testing prompt effectiveness, use online metrics (see IMPLEMENTATION_PLAN.md §9).

### What Gets Gated

Replay reuses recorded model responses, so the gate evaluates only what doesn't
depend on them, and blocks on everything else rather than passing silently:

| Change in the candidate | Gate behavior |
|---|---|
| Agent `budget`, `max_gate_attempts` | Replayed with the candidate's values; regressions block |
| Harness code | Replayed (both sides run the current code); regressions block |
| System prompts | **Inconclusive, blocks**: re-record golden tasks with the new prompt |
| Model routing (`config/models.yaml`) | **Inconclusive, blocks**: re-record golden tasks with the new model |
| Agent `tools`, `gates`, `model_tier`, `output_schema` | **Inconclusive, blocks**: replay stubs tools and defines no gate commands |
| A golden task missing from the candidate run | Counts as a regression |

Unset `--baseline-*` / `--candidate-*` paths default to the current `agents/` and
`config/models.yaml`. The JSON report has `inconclusive` and `blocked` fields.

### When to Run the Gate

Run the eval gate:
- ✅ Before merging prompt or model configuration changes
- ✅ Before deploying new agent definitions
- ✅ After harness code changes
- ✅ In CI on every PR that touches agents/ or config/

Don't run the gate:
- ❌ For code changes unrelated to agents or config
- ❌ For documentation-only changes

## Best Practices

1. **Always keep a baseline**: When changing agents/, keep a copy of the current version to use as baseline
2. **Test incrementally**: Change one thing at a time (prompt OR model, not both)
3. **Investigate regressions**: Don't just revert; understand why the regression occurred
4. **Add golden tasks**: If you find a regression, add it as a golden task
5. **Document changes**: Explain in the PR why pass rate changed (if it did)

## Troubleshooting

### "No golden tasks found"

The golden tasks directory is empty or doesn't exist. Check:
```bash
ls evals/golden/
```

You should see at least 5 `*.json` files. If not, see [EVALS.md](EVALS.md#creating-golden-tasks) for how to create them.

### Gate passes locally but fails in CI

Check:
- Are you using the same baseline in both environments?
- Are the golden tasks committed to git?
- Are dependencies the same (`pip install -e .[dev]`)?

### All tasks show as regressions

This usually means:
- The baseline or candidate configuration is broken
- Required files (prompts/, config/) are missing
- Check the detailed JSON report for error messages

## Further Reading

- [EVALS.md](EVALS.md): Evaluation harness and golden tasks
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): Overall factory architecture
- Phase 5 (§10): Continuous improvement with eval gating
