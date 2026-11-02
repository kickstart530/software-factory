# Golden Task Candidates

This directory contains candidate golden tasks generated from merged PRs.
Candidates require human or eval review before promotion to the active golden
set in `evals/golden/`.

## Workflow

1. **Generate**: Create candidates from PR fixtures using `factory golden generate`
2. **Review**: Inspect candidates with `factory golden list`
3. **Decide**: Promote with `factory golden promote` or reject with `factory golden reject`
4. **Execute**: Run promoted tasks once to capture real model responses

## Candidate Status

- `pending_review`: Awaiting human or eval review
- `approved`: Reviewed and promoted to active golden set
- `rejected`: Reviewed and rejected (not promoted)

## File Format

Each candidate is stored as a JSON file with:

- `id`: Unique candidate identifier
- `pr_number`: Source PR number
- `task`: Task definition (instructions, agent, title)
- `agent_spec`: Agent specification (tools, budget, gates)
- `expected_status`: Expected task outcome (usually "succeeded" for merged PRs)
- `metadata`: Source PR metadata, complexity estimates, generation timestamp
- `status`: Review status
- `reviewer`: Name of reviewer (when reviewed)
- `reviewed_at`: Review timestamp (when reviewed)
- `review_notes`: Optional notes from reviewer

## Example

```json
{
  "id": "candidate_123_abc12345",
  "pr_number": 123,
  "description": "Golden task from PR #123: Fix validation bug",
  "task": {
    "id": "candidate_123_abc12345",
    "work_order_id": "WO_PR_123",
    "agent": "implementer",
    "title": "Fix validation bug",
    "instructions": "Fix the email validation regex..."
  },
  "agent_spec": {
    "name": "implementer",
    "version": 1,
    "tools": ["read_file", "edit_file", "submit_result"],
    "budget": {"turns": 20, "usd": 5.0}
  },
  "expected_status": "succeeded",
  "expected_gates": [],
  "metadata": {
    "source_pr": {
      "number": 123,
      "title": "Fix validation bug",
      "author": "dev@example.com",
      "merged_at": "2026-11-01T12:00:00Z"
    },
    "complexity": {
      "complexity": "low",
      "files_count": 2,
      "total_changes": 45,
      "suggested_budget": {"turns": 20, "usd": 5.0}
    }
  },
  "created_at": "2026-11-02T10:00:00Z",
  "status": "pending_review"
}
```

## CLI Commands

### Generate candidate from PR fixture

```bash
factory golden generate tests/fixtures/pr_example.json
```

### List all candidates

```bash
factory golden list
factory golden list --status pending_review
factory golden list --json candidates.json
```

### Promote candidate

```bash
factory golden promote candidate_123_abc12345 --reviewer alice --notes "Good test case"
```

### Reject candidate

```bash
factory golden reject candidate_123_abc12345 --reviewer bob --reason "Too trivial"
```

## Best Practices

1. **Review carefully**: Not every merged PR makes a good golden task
2. **Diverse coverage**: Promote tasks that test different scenarios
3. **Complexity balance**: Include low, medium, and high complexity tasks
4. **Clear instructions**: Ensure task instructions are self-contained
5. **Capture real responses**: Run promoted tasks once to replace placeholders
6. **Regular cleanup**: Archive or remove rejected candidates periodically

## Notes

- Candidates are CLI-first (no MCP) per ADR-0009
- Promotion requires explicit human approval (no auto-promotion)
- This mirrors the memory lessons promotion workflow (IMPLEMENTATION_PLAN §7)
- PR fixtures in `tests/fixtures/` prevent live GitHub scraping in CI
