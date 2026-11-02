# Golden Task Generation from Merged PRs

Phase 5 continuous improvement: self-generated golden tasks from merged human PRs.

## Overview

This system mines merged pull requests to grow the golden eval set, keeping offline
evals fresh as the codebase evolves. Candidates require human or eval review before
promotion—no auto-promotion.

### Key Principles

1. **CLI-first** (NO MCP): All operations use the `factory` CLI, per ADR-0009
2. **Human/eval review gate**: Mirrors memory lessons promotion (IMPLEMENTATION_PLAN §7)
3. **No live GitHub in CI**: Tests use PR fixtures to avoid external dependencies
4. **Candidate → review → promote**: Three-stage workflow with explicit approval

## Architecture

```
Merged PR (fixture or metadata)
    ↓
factory golden generate (CLI)
    ↓
Candidate golden task (evals/candidates/)
    ↓
Human/eval review
    ↓
factory golden promote (CLI) ←─ Approved
    ↓
Active golden set (evals/golden/)
    ↓
Execute once to capture real model responses
    ↓
Part of regression suite
```

## Workflow

### 1. Generate Candidate

Create a candidate golden task from a PR fixture:

```bash
factory golden generate tests/fixtures/pr_simple_fix.json
```

The generator:
- Parses PR metadata (title, description, diff, files changed)
- Infers agent type (implementer, docs, reviewer, etc.)
- Extracts task instructions
- Estimates complexity and suggests budget
- Creates candidate in `evals/candidates/`

### 2. Review Candidates

List all candidates:

```bash
factory golden list
factory golden list --status pending_review
factory golden list --json candidates.json
```

Review criteria:
- **Clarity**: Are instructions self-contained and unambiguous?
- **Diversity**: Does it test a scenario not covered by existing golden tasks?
- **Complexity**: Is it too trivial or too complex?
- **Value**: Will it catch regressions or test important harness behavior?

### 3. Promote or Reject

Approve and promote:

```bash
factory golden promote candidate_123_abc12345 \
  --reviewer alice \
  --notes "Good test for validation edge cases"
```

Reject:

```bash
factory golden reject candidate_123_abc12345 \
  --reviewer bob \
  --reason "Too trivial - one-line fix with no interesting behavior"
```

### 4. Capture Real Responses

Promoted golden tasks have placeholder `model_responses`. Run them once to capture:

```bash
factory run \
  --task evals/golden/pr_123_implementer.json \
  --workspace /path/to/test/repo \
  --state-dir .factory-capture
```

Then update the golden task's `model_responses` with the captured transcript.

## PR Fixture Format

Fixtures in `tests/fixtures/*.json` follow this schema:

```json
{
  "number": 123,
  "title": "Fix email validation regex",
  "description": "The current email validation...",
  "author": "developer@example.com",
  "merged_at": "2026-10-15T14:30:00Z",
  "base_branch": "main",
  "labels": ["bug", "validation"],
  "files_changed": ["validators.py", "tests/test_validators.py"],
  "additions": 15,
  "deletions": 8,
  "diff": "diff --git a/validators.py b/validators.py\n..."
}
```

### Creating Fixtures

To create a fixture from a real merged PR:

```bash
# Using gh CLI (not in CI - manual workflow)
gh pr view 123 --json number,title,body,author,mergedAt,baseRefName,labels \
  > pr_123_metadata.json

gh pr diff 123 > pr_123.diff
```

Then combine into the fixture format with `files_changed`, `additions`, `deletions`.

For CI tests, hand-craft minimal fixtures that cover key scenarios (simple fix,
complex feature, docs update, refactor, etc.).

## Candidate Structure

Each candidate in `evals/candidates/` contains:

```json
{
  "id": "candidate_123_abc12345",
  "pr_number": 123,
  "description": "Golden task from PR #123: Fix validation bug",
  "task": {
    "id": "candidate_123_abc12345",
    "work_order_id": "WO_PR_123",
    "agent": "implementer",
    "title": "Fix email validation regex",
    "instructions": "Fix the email validation regex..."
  },
  "agent_spec": {
    "name": "implementer",
    "version": 1,
    "tools": ["read_file", "edit_file", "submit_result"],
    "budget": {"turns": 20, "usd": 5.0},
    "gates": [],
    ...
  },
  "expected_status": "succeeded",
  "expected_gates": [],
  "metadata": {
    "source_pr": {
      "number": 123,
      "title": "Fix validation bug",
      "author": "dev@example.com",
      "merged_at": "2026-11-01T12:00:00Z",
      "labels": ["bug", "validation"]
    },
    "complexity": {
      "complexity": "low",
      "files_count": 2,
      "total_changes": 23,
      "suggested_budget": {"turns": 20, "usd": 5.0}
    },
    "files_changed": ["validators.py", "tests/test_validators.py"],
    "generation_timestamp": "2026-11-02T10:00:00Z"
  },
  "created_at": "2026-11-02T10:00:00Z",
  "status": "pending_review",
  "reviewer": null,
  "reviewed_at": null,
  "review_notes": null
}
```

### Status States

- `pending_review`: Awaiting human or eval review
- `approved`: Reviewed and promoted to active golden set
- `rejected`: Reviewed and not promoted

## Agent Type Inference

The generator infers agent type from PR characteristics:

| Indicator | Agent Type |
|-----------|------------|
| Only test files changed | `test_engineer` |
| Only docs files (.md, .rst) | `docs` |
| "review", "refactor", "cleanup" in title/desc | `implementer` |
| Default | `implementer` |

Override with `--agent`:

```bash
factory golden generate pr_fixture.json --agent reviewer
```

## Complexity Estimation

Heuristics based on changes and files:

| Changes | Files | Complexity | Budget |
|---------|-------|------------|--------|
| <50 | ≤2 | low | 20 turns, $5 |
| <200 | ≤5 | medium | 40 turns, $10 |
| ≥200 or >5 files | high | 80 turns, $20 |

## Best Practices

### What Makes a Good Golden Task?

1. **Self-contained**: Instructions don't assume context from other PRs
2. **Deterministic**: Same inputs produce same outputs
3. **Focused**: Tests one scenario or edge case
4. **Representative**: Reflects real work the factory will encounter
5. **Verifiable**: Clear success criteria

### What to Reject

- Trivial one-line changes with no interesting behavior
- PRs that only update dependencies or lockfiles
- Changes that require deep context from other PRs or issues
- Overly complex changes that should be broken into smaller tasks
- PRs with unclear or ambiguous requirements

### Diversity Goals

Aim for coverage across:
- Agent types (implementer, reviewer, docs, test engineer)
- Complexity levels (low, medium, high)
- Change types (bug fix, feature, refactor, docs)
- File types (Python, YAML, Markdown, config)
- Gates (build, test, lint, typecheck)

## Integration with CI

The test suite uses fixtures to avoid live GitHub scraping:

```yaml
# .github/workflows/ci.yml
- name: Test golden task generation
  run: |
    pytest tests/test_golden_gen.py -v
```

Tests verify:
- PR parsing (diff, metadata extraction)
- Agent type inference
- Complexity estimation
- Candidate generation
- Promotion workflow
- CLI integration

## Future Enhancements

### Automatic Fixture Capture (Phase 5+)

A GitHub App webhook could auto-capture merged PR metadata:

```python
# Future: GitHub App webhook handler
@app.route('/webhook/pr_merged', methods=['POST'])
def on_pr_merged():
    pr = github.get_pr(pr_number)
    fixture = create_pr_fixture(pr)
    save_fixture(fixture, 'fixtures/auto_captured/')
    # Still requires manual review and promotion
```

### Eval-Based Review (Phase 5+)

An eval could auto-review candidates based on rubrics:

```python
# Future: eval-based review
def eval_candidate_quality(candidate: CandidateGoldenTask) -> bool:
    """Return True if candidate meets quality bar."""
    checks = [
        instructions_are_clear(candidate),
        not_too_trivial(candidate),
        not_duplicate(candidate, existing_golden_tasks),
        complexity_reasonable(candidate),
    ]
    return all(checks)
```

Still requires human approval after eval passes.

## Troubleshooting

### "Candidate not found"

The candidate ID must match exactly. List candidates to see IDs:

```bash
factory golden list
```

### "Cannot promote rejected candidate"

Rejected candidates cannot be promoted. If you want to reconsider, manually
edit the candidate JSON to set `status: "pending_review"`, then promote.

### "No PR fixture file found"

Ensure the fixture path is correct and the file exists. Fixtures must be
JSON files with the required fields (see "PR Fixture Format").

### Generated task has wrong agent type

Override with `--agent`:

```bash
factory golden generate pr.json --agent reviewer
```

Or edit the candidate JSON before promotion.

## See Also

- [EVALS.md](EVALS.md): Evaluation harness and golden task format
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): Phase 5 roadmap (§9-§10)
- [ADR-0009](adr/ADR-0009-cli-first-external-tools.md): CLI-first tooling decision
- [evals/candidates/README.md](../evals/candidates/README.md): Candidate directory structure
