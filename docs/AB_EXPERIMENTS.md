# A/B Model Routing (Phase 5)

The software-factory harness supports controlled A/B testing of model tiers and providers via experiment configuration. This allows you to:

- Compare different models or effort levels for the same tier
- Route traffic deterministically based on work order or tenant
- Score experiments via telemetry to make data-driven decisions

## Quick Start

1. **Define an experiment** in `config/experiments.yaml`:

```yaml
experiments:
  mid-tier-effort-test:
    enabled: true
    tier: mid
    sticky_by: work_order_id
    variants:
      control:
        weight: 50
        model: claude-opus-5-5
        effort: medium
      treatment:
        weight: 50
        model: claude-opus-5-5
        effort: high
```

2. **Run tasks** as normal. The harness automatically routes requests according to the experiment configuration.

3. **Query telemetry** to score the experiment:

```bash
# Find all model.call events with experiment metadata
cat .factory-runs/traces/*.jsonl | jq 'select(.event == "model.call" and .experiment_id == "mid-tier-effort-test")'
```

4. **Update the config** after the experiment concludes:
   - Set `enabled: false` to stop the experiment
   - Remove the entry to clean up
   - Update the tier's default in `config/models.yaml` if the treatment won

## Experiment Configuration

### Structure

Each experiment in `config/experiments.yaml` has:

- `enabled` (boolean): Whether the experiment is active
- `tier` (string): Which model tier this experiment applies to (e.g., `frontier`, `mid`, `small`)
- `sticky_by` (string or null): How to assign variants deterministically
  - `"work_order_id"`: Same work order always gets the same variant
  - `"tenant_id"`: Same tenant always gets the same variant
  - `null`: Random assignment per model call (not recommended for most experiments)
- `variants` (dict): Named variants with their configuration
  - `weight` (int): Relative weight for traffic allocation
  - `model` (string): Model identifier to use
  - `effort` (string): Effort level (`low`, `medium`, `high`)

### Example: Model Comparison

Compare two different models for the frontier tier:

```yaml
experiments:
  frontier-model-compare:
    enabled: true
    tier: frontier
    sticky_by: work_order_id
    variants:
      opus:
        weight: 50
        model: claude-opus-5-5
        effort: high
      sonnet:
        weight: 50
        model: claude-sonnet-4-5
        effort: high
```

### Example: Effort Level Test

Test whether higher effort improves results for the mid tier:

```yaml
experiments:
  mid-effort-test:
    enabled: true
    tier: mid
    sticky_by: work_order_id
    variants:
      control:
        weight: 80
        model: claude-opus-5-5
        effort: medium
      treatment:
        weight: 20
        model: claude-opus-5-5
        effort: high
```

### Example: Multi-Variant Test

Test three different configurations:

```yaml
experiments:
  small-tier-test:
    enabled: true
    tier: small
    sticky_by: work_order_id
    variants:
      baseline:
        weight: 50
        model: claude-opus-5-5
        effort: low
      faster:
        weight: 25
        model: claude-sonnet-4-5
        effort: medium
      premium:
        weight: 25
        model: claude-opus-5-5
        effort: medium
```

## CLI Commands

### List All Experiments

```bash
factory experiments list
```

Shows all defined experiments with their status, tier, sticky assignment strategy, and variants.

### Show Experiment Details

```bash
factory experiments show <experiment-id>
```

Displays the full configuration for a specific experiment as JSON.

### Test Assignment

```bash
factory experiments test-assignment <experiment-id> <assignment-key>
```

Shows which variant would be assigned for a given work order ID or tenant ID. Useful for understanding deterministic routing.

Example:

```bash
factory experiments test-assignment mid-effort-test wo-12345
```

Output:

```
Experiment: mid-effort-test
Assignment key: wo-12345
Sticky by: work_order_id

Assigned variant: treatment
  Model:  claude-opus-5-5
  Effort: high
  Weight: 20

Variant distribution:
    control: 80.0% (weight 80)
  ← treatment: 20.0% (weight 20)
```

## Telemetry Integration

When an experiment is active, every `model.call` event includes:

- `experiment_id`: The experiment identifier (e.g., `"mid-effort-test"`)
- `variant`: The variant name that was selected (e.g., `"control"` or `"treatment"`)

### Example Event

```json
{
  "ts": 1698765432.0,
  "event": "model.call",
  "task_id": "task-123",
  "model": "claude-opus-5-5",
  "stop_reason": "end_turn",
  "input_tokens": 1500,
  "output_tokens": 300,
  "cache_read_tokens": 500,
  "experiment_id": "mid-effort-test",
  "variant": "treatment"
}
```

### Querying Experiments

Use `jq` to analyze experiment results from JSONL traces:

```bash
# Count calls per variant
cat .factory-runs/traces/*.jsonl | \
  jq -r 'select(.event == "model.call" and .experiment_id == "mid-effort-test") | .variant' | \
  sort | uniq -c

# Average token usage per variant
cat .factory-runs/traces/*.jsonl | \
  jq -r 'select(.event == "model.call" and .experiment_id == "mid-effort-test") | 
         [.variant, .input_tokens + .output_tokens] | @csv'
```

If using OpenTelemetry export, query spans with attributes `experiment_id` and `variant` in your observability platform.

## Routing Behavior

### Sticky Assignment

When `sticky_by` is set to `"work_order_id"` or `"tenant_id"`, the harness uses deterministic hashing to assign variants:

1. Construct a hash input: `"{tier}:{assignment_key}"` (e.g., `"mid:wo-12345"`)
2. Compute SHA-256 hash of the input
3. Map hash to a position in the weight distribution
4. Select the variant at that position

This ensures:

- The same work order or tenant always gets the same variant
- Traffic is distributed according to variant weights over many keys
- No central state is required

### Random Assignment

When `sticky_by` is `null`, each model call randomly selects a variant according to weights. This is rarely useful for experiments (no stability for comparison) but can support one-off tests.

### No Experiment

If no enabled experiment exists for a tier, requests use the tier's default model and effort from `config/models.yaml`.

### Multiple Experiments

**Only one experiment per tier** is supported. If multiple enabled experiments target the same tier, the first one in the YAML file is used.

## Best Practices

### 1. Use Sticky Assignment

For meaningful experiments, always set `sticky_by` to `"work_order_id"` or `"tenant_id"`. Random per-call assignment makes it difficult to attribute outcomes.

### 2. Start Small

Use unequal weights (e.g., 80/20 or 90/10) when testing a new model or effort level. This limits exposure to potential regressions.

### 3. Measure First

Before running an experiment, ensure you have baseline metrics for the control variant from historical telemetry.

### 4. Define Success Criteria

Know what you're optimizing for before starting:

- Task success rate
- Token usage
- Gate pass rate
- Human escalation rate

### 5. Run to Significance

Collect enough samples to draw statistically valid conclusions. For a 50/50 split, aim for at least 100 work orders per variant.

### 6. Update Promptly

After concluding an experiment:

- Disable it by setting `enabled: false`
- If the treatment won, update the tier's default in `config/models.yaml`
- Archive the experiment configuration for future reference

### 7. Validate in Evals

Before promoting a winning variant to production, re-run the evaluation suite with the new model/effort to catch any regressions on known tasks.

## Limitations

- **One experiment per tier**: Only the first enabled experiment for a tier is active
- **No multi-tier experiments**: An experiment applies to a single tier
- **No runtime updates**: Changes to `config/experiments.yaml` require restarting the harness or supervisor
- **No automatic analysis**: Scoring requires manual telemetry queries or observability platform dashboards

## Future Enhancements

Potential Phase 5+ improvements:

- Automated experiment scoring with statistical significance testing
- Experiment scheduling (auto-enable/disable based on time windows)
- Per-repo or per-agent experiment overrides
- Integration with the eval suite for automated quality gates
- Web UI for experiment management and results visualization
