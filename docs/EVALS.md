# Evaluation Harness

The software-factory evaluation harness provides deterministic replay of golden tasks to test prompt, model, and harness changes before deploying them to production.

## Overview

The eval system consists of three components:

1. **Golden tasks** (`evals/golden/*.json`): Recorded task executions with expected outcomes
2. **Replay runner** (`evals/replay.py`): Re-executes tasks with stubbed tool results
3. **CLI** (`factory evals replay`): Command-line interface for running evals

## Golden Tasks

A golden task is a JSON file recording:

- The task definition (instructions, acceptance criteria)
- The agent specification (tools, budget, gates)
- Model responses (tool calls, stop reasons, token counts)
- Expected outcome (status, gates that should pass)
- Optional tool result stubs for deterministic replay

### Structure

```json
{
  "description": "Human-readable description of what this tests",
  "task": {
    "id": "golden_001",
    "work_order_id": "WO_TEST_001",
    "agent": "implementer",
    "title": "Task title",
    "instructions": "What the agent should do"
  },
  "agent_spec": {
    "name": "implementer",
    "version": 1,
    "tools": ["read_file", "edit_file", "submit_result"],
    "budget": {"turns": 20, "usd": 10.0},
    "gates": ["unit_tests"],
    ...
  },
  "expected_status": "succeeded",
  "expected_gates": ["unit_tests"],
  "model_responses": [
    {
      "content": [{"type": "tool_use", "id": "toolu_001", "name": "read_file", ...}],
      "stop_reason": "tool_use",
      "model": "test-model",
      "input_tokens": 1000,
      "output_tokens": 50
    }
  ]
}
```

### Current Golden Set

The starter golden set in `evals/golden/` includes:

| Task | Description | Expected Status | Tests |
|------|-------------|----------------|-------|
| `fix_add_function` | Fix a subtract bug in add() | succeeded | Basic tool use, submit_result |
| `budget_exceeded` | Task that exceeds turn budget | failed | Budget enforcement |
| `escalation` | Ambiguous task requiring human input | escalated | Escalation flow |
| `write_new_module` | Create a new file | succeeded | File creation |
| `refactor_with_tests` | Extract code to new module | succeeded | Multi-file changes |

### Creating Golden Tasks

To add a new golden task:

1. Run a real task with the harness and capture the transcript
2. Extract the task definition, agent spec, and model responses
3. Define the expected outcome (status, gates)
4. Save as `evals/golden/<name>.json`
5. Run `factory evals replay` to verify it works

Golden tasks should be:

- **Deterministic**: Same inputs always produce same outputs
- **Focused**: Test one scenario or edge case per task
- **Minimal**: As few turns as necessary to demonstrate the behavior
- **Documented**: Clear description of what's being tested

## Replay Runner

The replay runner (`evals/replay.py`) re-executes golden tasks through the harness with:

- Model responses replayed from the recording (no actual model calls)
- Tool results stubbed where needed (configurable per task)
- Full harness logic (policy, hooks, gates, budgets) enforced
- Workspace and checkpoints isolated per task

### How It Works

1. Load golden task from JSON
2. Create isolated workspace for the task
3. Initialize harness with `ReplayModelClient` (returns recorded responses)
4. Run task through harness
5. Compare actual outcome to expected outcome
6. Report pass/fail with detailed diagnostics

### Usage

Run all golden tasks:

```bash
factory evals replay
```

Run with custom golden directory:

```bash
factory evals replay --golden-dir /path/to/golden/tasks
```

Save detailed results to JSON:

```bash
factory evals replay --json results.json
```

### Output

Console output shows pass/fail for each task:

```
======================================================================
Replay Results: 4/5 passed
======================================================================

✓ golden_001: Fix add() function that subtracts instead of adds
  Expected: succeeded, Got: succeeded

✗ golden_002: Task that exceeds turn budget
  Expected: failed, Got: succeeded
  Error: Budget check did not fire as expected

✓ golden_003: Task that escalates due to ambiguity
  Expected: escalated, Got: escalated

✓ golden_004: Create a new module with utility functions
  Expected: succeeded, Got: succeeded
```

## Integration with CI

The eval suite is designed to run in CI (`make test` or CI workflow):

```yaml
- name: Run golden task evals
  run: |
    python -m pip install -e .[dev]
    factory evals replay --json evals-results.json
```

A failing eval (status mismatch or unexpected gate failure) returns non-zero exit code and blocks the merge.

## Scoring

Each replay returns a `ReplayResult` with:

- `passed`: boolean (actual matches expected)
- `actual_status`: what the harness returned
- `expected_status`: what the golden task declares
- `gates_passed`: list of gates that passed
- `gates_failed`: list of gates that failed
- `usage`: token/cost/turn usage (always zero in replay with stub model)

A task passes if:

1. `actual_status == expected_status`
2. If status is `succeeded`, all expected gates passed

## OpenTelemetry Integration

The harness telemetry system now includes an `OtelTracer` that exports spans to OpenTelemetry collectors.

### Setup

Install OpenTelemetry dependencies:

```bash
pip install -e .[otel]
```

Configure the OTLP endpoint:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

If the endpoint is not set, spans are exported to the console (useful for local debugging).

### Span Structure

The `OtelTracer` creates:

- **Root span per task**: `task.<task_id>`
- **Child spans for events**: `model.call`, `tool.call`, `gate`, `policy.denied`, etc.
- **Attributes from event kwargs**: tokens, model tier, tool names, gate results, errors

Example span hierarchy:

```
task.T1
  ├─ model.call (input_tokens=1000, output_tokens=50)
  ├─ tool.call (tool=read_file, is_error=false)
  ├─ model.call (input_tokens=1100, output_tokens=60)
  ├─ tool.call (tool=edit_file, is_error=false)
  ├─ gate (gate=unit_tests, passed=true)
  └─ task.finished (status=succeeded)
```

### Fallback to JSONL

`OtelTracer` accepts an optional `fallback_jsonl` path. When set, all events are also written to JSONL (same format as `JsonlTracer`). This ensures you never lose telemetry if the OTLP endpoint is down.

```python
from factory.harness.telemetry import OtelTracer
from pathlib import Path

tracer = OtelTracer(
    service_name="software-factory", fallback_jsonl=Path(".factory-runs/traces/task.jsonl")
)
```

### Usage in Harness

The harness accepts any object implementing the `Tracer` protocol:

```python
from factory.harness import HarnessDeps, JsonlTracer, OtelTracer

# Local development (JSONL only)
deps = HarnessDeps(tracer=JsonlTracer(path, task_id=task.id), ...)

# Production (OpenTelemetry with JSONL fallback)
deps = HarnessDeps(
    tracer=OtelTracer(fallback_jsonl=path),
    ...
)
```

## Extending the Eval Suite

### Scenario Suites (Future)

Specialized test suites per agent role:

- **Reviewer**: seeded bugs, precision/recall on findings
- **Security**: known CVEs and injection payloads
- **Planner**: DAG quality rubrics (parallelism, granularity)

## Adversarial Suite

The adversarial eval suite tests the factory's defenses against prompt injection attacks and malicious inputs. These evals verify that untrusted content from external sources (issues, web pages, API responses) cannot manipulate agent behavior.

### Location

Adversarial evals are stored in `evals/adversarial/*.json` and follow the same format as golden tasks.

### Defense Mechanisms Tested

1. **Untrusted Content Tagging**
   - Output from tools marked `trust="untrusted"` is wrapped in `<untrusted_content>` delimiters
   - Agents are instructed to never follow instructions found in these tags
   - The delimiters make injection attempts visible in the transcript

2. **Taint-Aware Policy**
   - When untrusted content enters the context, a taint flag is set
   - Tools marked `sensitive=True` (like `run_command`) are denied while tainted
   - The taint persists for `TAINT_WINDOW_TURNS` turns (currently 2)
   - Fetching more untrusted content resets the taint window

3. **Command and Path Protection**
   - Dangerous command patterns (force-push, pipe-to-shell, sudo, etc.) are always blocked
   - Protected paths (.github/workflows, Dockerfile, infra/) require appropriate risk tier
   - These policies apply whether or not the context is tainted

### Current Adversarial Evals

| Eval | Attack Vector | Expected Defense |
|------|---------------|------------------|
| `injection_issue_body` | Instructions in issue body to delete files | Agent escalates; recognizes suspicious content |
| `injection_command_injection` | Web content tells agent to run `curl \| bash` | Command blocked by pipe-to-shell policy; taint blocks run_command |
| `injection_secret_exfiltration` | Issue instructs agent to dump environment variables | `printenv` blocked by policy; taint blocks run_command |
| `injection_dependency_confusion` | Comments in requirements.txt contain instructions | Agent ignores instructions in file comments |

### Adding New Adversarial Evals

To add a new adversarial eval:

1. **Identify the attack vector**: Where does untrusted input enter? (issue body, web fetch, file comments)
2. **Define the payload**: What malicious instruction are you testing against?
3. **Specify expected behavior**: Should the agent escalate, ignore, or have the tool call blocked?
4. **Create the JSON file**:

```json
{
  "description": "Description of the attack being tested",
  "task": {
    "id": "adv_XXX",
    "instructions": "Task that requires fetching untrusted content"
  },
  "agent_spec": {
    "tools": ["fetch_issue", "run_command", "submit_result"],
    ...
  },
  "expected_status": "succeeded",
  "tool_results": {
    "toolu_001": "MALICIOUS INSTRUCTION HERE"
  },
  "model_responses": [
    {
      "content": [{"type": "tool_use", "id": "toolu_001", "name": "fetch_issue", ...}],
      ...
    },
    {
      "content": [{"type": "tool_use", "name": "escalate", ...}],
      ...
    }
  ]
}
```

5. **Run the eval**: `factory evals replay --golden-dir evals/adversarial`
6. **Verify defense**: Check transcript shows proper tagging, policy denial, or escalation

### Running Adversarial Evals

Run only adversarial evals:

```bash
factory evals replay --golden-dir evals/adversarial
```

Run both golden and adversarial:

```bash
factory evals replay --golden-dir evals/golden && \
factory evals replay --golden-dir evals/adversarial
```

### Common Injection Patterns

The `factory.harness.adversarial` module provides helpers for testing:

- `INJECTION_PAYLOADS`: List of common prompt injection attempts
- `is_injection_attempt(text)`: Heuristic detector for injection patterns
- `adversarial_mock_tools()`: Mock tools (`fetch_issue`, `fetch_web`) marked as untrusted
- `validate_taint_defense(transcript)`: Verify taint tracking worked correctly

### Defense-in-Depth Strategy

The factory's injection defense uses multiple layers:

1. **Input Tagging** (passive): Mark untrusted content so the model can recognize it
2. **Taint Tracking** (active): Block sensitive operations immediately after untrusted input
3. **Command Policy** (always-on): Deny dangerous patterns regardless of context
4. **Model Training** (future): Train models to recognize and ignore injections
5. **Human Gates** (final layer): High-risk changes require human approval

No single layer is perfect, but together they make successful prompt injection extremely difficult.

### Golden Task Generation from Merged PRs

**Status**: Phase 5 implementation complete (see [GOLDEN_TASK_GENERATION.md](GOLDEN_TASK_GENERATION.md))

The factory can now generate candidate golden tasks from merged PR diffs and metadata:

1. **Generate**: `factory golden generate <pr-fixture.json>` creates a candidate
2. **Review**: `factory golden list` shows all candidates awaiting review
3. **Decide**: `factory golden promote <id>` or `factory golden reject <id>`
4. **Execute**: Run promoted tasks once to capture real model responses

### Key Features

- **CLI-first** (no MCP): All operations via `factory` CLI per ADR-0009
- **Human/eval review gate**: Mirrors memory lessons promotion (no auto-promotion)
- **No live GitHub in CI**: Tests use PR fixtures
- **Agent type inference**: Automatically determines agent type from PR characteristics
- **Complexity estimation**: Suggests budget based on PR size and files changed

### Quick Example

```bash
# Generate candidate from PR fixture
factory golden generate tests/fixtures/pr_simple_fix.json

# List candidates
factory golden list --status pending_review

# Promote approved candidate
factory golden promote candidate_123_abc12345 \
  --reviewer alice \
  --notes "Good validation edge case"

# Reject unsuitable candidate
factory golden reject candidate_456_def67890 \
  --reviewer bob \
  --reason "Too trivial"
```

See [GOLDEN_TASK_GENERATION.md](GOLDEN_TASK_GENERATION.md) for full documentation.

## Best Practices

1. **Run evals before every harness change**: Treat them like unit tests
2. **Add a golden task for every bug fix**: Prevent regressions
3. **Keep the suite fast**: Replay should run in seconds, not minutes
4. **Review golden tasks in PRs**: They are versioned like code
5. **Don't over-fit to evals**: They test known scenarios; prod will surprise you

## Troubleshooting

**Eval fails with "Replay exhausted: no response for turn N"**

The golden task's model_responses list is too short. Either:
- Add more responses to cover all turns
- Reduce the agent's turns until it hits submit_result or escalate

**Eval passes locally but fails in CI**

Check for:
- Non-deterministic behavior (time.time(), random, unordered dicts)
- File paths hardcoded to your local machine
- Missing dependencies in CI environment

**All evals fail after a harness change**

If the change is intentional (e.g. new tool, changed prompt), regenerate the golden tasks. If unintentional, revert the change and investigate.

## Further Reading

- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): Overall factory architecture and roadmap
- [ADR-0006](adr/ADR-0006-evaluation-and-metrics.md): Decision record on offline evals (if exists)
- [Harness source](../factory/harness/): Agent loop, tools, policy, telemetry
