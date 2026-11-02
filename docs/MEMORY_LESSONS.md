# Memory Lessons

## Overview

The **Memory Lessons** system captures distilled, reviewed learnings from past
factory runs. Lessons are insights like "repo X needs `make gen` before tests"
or "authentication changes require security review" that can help future agents
avoid repeating mistakes and leverage accumulated knowledge.

### Key Principles

1. **No auto-promotion**: Lessons start as candidates and require explicit human
   or eval approval before being promoted for use.

2. **Provenance tracking**: Every lesson records where it came from (run ID,
   work order ID) for audit trails.

3. **Time-to-live**: Candidate lessons expire if not reviewed within their TTL
   (default 90 days), preventing unbounded growth of unreviewed content.

4. **Security-first**: Lessons must never contain secrets, credentials, or PII.
   The system enforces text length limits and review workflows to prevent misuse.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Factory Run or Human                                            │
│   │                                                             │
│   ├─► propose_lesson() ──► CANDIDATE (with TTL + provenance)  │
│   │                              │                              │
│   │                              ▼                              │
│   │                      Review Required                        │
│   │                              │                              │
│   │                    ┌─────────┴─────────┐                   │
│   │                    ▼                   ▼                    │
│   │              promote()              reject()                │
│   │                    │                   │                    │
│   │                    ▼                   ▼                    │
│   │              PROMOTED              REJECTED                 │
│   │                    │                                        │
│   │                    ▼                                        │
│   └──────────► query() returns promoted only (by default)      │
│                                                                 │
│ Maintenance: expire_candidates() moves expired → EXPIRED       │
│              cleanup_expired() deletes old EXPIRED             │
└─────────────────────────────────────────────────────────────────┘
```

## Data Model

### Lesson

```python
{
  "id": "lesson-20260102123045-a1b2c3d4",
  "text": "In repo X, run `make gen` before tests to regenerate code",
  "repo": "https://github.com/example/repo-x",
  "tags": ["testing", "codegen", "prerequisites"],
  
  # Provenance
  "run_id": "bc_abc123",
  "work_order_id": "wo-2024-001",
  
  # Lifecycle
  "status": "promoted",  # candidate | promoted | rejected | expired
  "created_at": "2026-01-02T12:30:45Z",
  "ttl_days": 90,
  
  # Review metadata
  "reviewed_by": "alice@example.com",
  "reviewed_at": "2026-01-05T09:00:00Z",
  "review_reason": "verified by successful eval runs"
}
```

### Status Flow

- **candidate**: Newly proposed, awaiting review. Visible to admins but not
  included in default queries. Expires after TTL if not reviewed.

- **promoted**: Approved for use. Returned by default `query()` calls.

- **rejected**: Explicitly marked as incorrect or unhelpful. Not returned by
  queries unless explicitly requested.

- **expired**: Was a candidate but TTL elapsed without review. Not returned by
  queries; eligible for cleanup after a longer retention period.

## CLI Usage

All lesson operations use the `factory memory` CLI (no MCP).

### Propose a Lesson

```bash
# Minimal proposal
factory memory propose --text "Always run `make gen` before tests in repo X"

# With metadata
factory memory propose \
  --text "Authentication changes require security review" \
  --repo "https://github.com/example/auth-service" \
  --tags security authentication review \
  --run-id bc_abc123 \
  --work-order-id wo-2024-001 \
  --ttl-days 90
```

**Output:**
```
✓ Proposed lesson lesson-20260102123045-a1b2c3d4
{
  "id": "lesson-20260102123045-a1b2c3d4",
  "text": "Always run `make gen` before tests in repo X",
  "status": "candidate",
  "created_at": "2026-01-02T12:30:45Z",
  ...
}
```

### List Lessons

```bash
# List candidates awaiting review
factory memory list candidates

# List promoted lessons
factory memory list promoted

# List rejected lessons
factory memory list rejected

# List expired lessons
factory memory list expired
```

### Promote a Lesson

```bash
factory memory promote lesson-20260102123045-a1b2c3d4 \
  --reason "verified by 3 successful eval runs" \
  --user alice@example.com
```

### Reject a Lesson

```bash
factory memory reject lesson-20260102123045-a1b2c3d4 \
  --reason "incorrect - tests auto-regenerate code" \
  --user bob@example.com
```

### Query Lessons

```bash
# Query promoted lessons (default)
factory memory query

# Query by repository
factory memory query --repo "https://github.com/example/repo-x"

# Query by tags (must have ALL tags)
factory memory query --tags testing codegen

# Query candidates explicitly
factory memory query --status candidate

# Query all statuses
factory memory query --status all

# Include expired candidates
factory memory query --status candidate --include-expired
```

### Cleanup

```bash
# Expire candidates past their TTL
factory memory cleanup

# Also delete expired lessons older than 365 days
factory memory cleanup --delete-older-than-days 365
```

## Integration Patterns

### Agent Post-Run Reflection

After a task completes, a reflection step can propose lessons:

```python
from factory.memory import FileBackedLessonStore, LessonService, LessonProposal

store = FileBackedLessonStore(Path(".factory-runs/memory"))
service = LessonService(store)

# Agent or post-processor proposes a lesson
proposal = LessonProposal(
    text="Repo X requires `make gen` before tests",
    repo=task.repo.url,
    tags=["testing", "codegen"],
    run_id=context.run_id,
    work_order_id=task.work_order_id,
)
lesson = service.propose_lesson(proposal)
# Lesson is now a CANDIDATE awaiting review
```

### Eval-Based Promotion

An eval suite can automatically promote lessons that prove helpful:

```python
# Run evals with and without the lesson
results_with = run_evals_with_lesson(lesson)
results_without = run_evals_without_lesson(lesson)

if results_with.success_rate > results_without.success_rate + threshold:
    decision = PromotionDecision(
        lesson_id=lesson.id,
        approved=True,
        reason=f"improved success rate from {results_without.success_rate:.1%} to {results_with.success_rate:.1%}",
        decided_by="eval-service",
    )
    service.promote(decision)
```

### Query in Agent Context

Agents query lessons at the start of a task:

```python
from factory.memory import LessonQuery

query = LessonQuery(
    repo=task.repo.url,
    tags=["testing"],  # contextual tags from task type
    status=LessonStatus.PROMOTED,  # default
)
lessons = service.query(query)

# Add to agent context as structured knowledge
context.add_lessons([lesson.text for lesson in lessons])
```

## Security

### No Secrets in Lessons

Lessons must NEVER contain:
- API keys, tokens, passwords, or credentials
- Personally identifiable information (PII)
- Internal IP addresses, hostnames, or infrastructure details
- Sensitive business logic or proprietary algorithms

**Enforcement:**
- Text length limit (10,000 characters) prevents large pastes
- Review workflow requires human/eval approval before use
- Provenance tracking enables audit of lesson sources
- Lessons are stored in plain text files for easy inspection

### Review Best Practices

Before promoting a lesson:

1. **Verify correctness**: Is the advice accurate and current?
2. **Check for secrets**: Scan for patterns like API keys, tokens, passwords
3. **Assess scope**: Does it apply narrowly (one repo) or broadly?
4. **Consider staleness**: Will this become outdated? Adjust TTL accordingly
5. **Audit provenance**: Which run/work order generated this? Was it trusted?

## Storage

### File-Backed Store (v1)

Lessons are stored as JSONL files organized by status:

```
.factory-runs/memory/
├── candidate/
│   ├── lesson-20260102123045-a1b2c3d4.jsonl
│   └── lesson-20260103140000-e5f6g7h8.jsonl
├── promoted/
│   ├── lesson-20260101100000-i9j0k1l2.jsonl
│   └── lesson-20260102090000-m3n4o5p6.jsonl
├── rejected/
│   └── lesson-20260103150000-q7r8s9t0.jsonl
└── expired/
    └── lesson-20251201000000-u1v2w3x4.jsonl
```

**Properties:**
- Easy to inspect and backup
- Fast local iteration and testing
- No external database dependency

### Future: Postgres/pgvector Store

For production multi-tenancy, a Postgres store can implement the `LessonStore`
protocol:

- **Postgres**: structured storage, indexes, tenant isolation
- **pgvector**: semantic search over lesson embeddings
- **RLS**: row-level security for multi-tenant isolation

The service layer is unchanged; only the store implementation swaps.

## Lifecycle Management

### TTL and Expiry

Candidate lessons have a TTL (default 90 days). If not reviewed within the TTL:

1. `expire_candidates()` marks them as EXPIRED
2. They no longer appear in candidate lists
3. After a longer retention (e.g., 365 days), `cleanup_expired()` deletes them

This prevents unbounded growth of unreviewed content.

### Maintenance Cron

Run periodic maintenance:

```bash
# Daily: expire candidates past TTL
factory memory cleanup

# Monthly: delete expired lessons older than 365 days
factory memory cleanup --delete-older-than-days 365
```

## Metrics

Track these metrics to measure lesson system health:

- **Candidate backlog**: Number of lessons awaiting review
- **Promotion rate**: % of candidates promoted vs. rejected
- **Query hit rate**: % of agent queries that return >0 lessons
- **Eval impact**: Success rate delta when lessons are included vs. omitted
- **Staleness**: Age distribution of promoted lessons

## Future Enhancements

1. **Vector search**: Semantic similarity search with pgvector embeddings
2. **Lesson versioning**: Update promoted lessons with new information
3. **Deprecation workflow**: Mark promoted lessons as obsolete when no longer valid
4. **Bulk review UI**: Web interface for reviewing candidate backlogs
5. **Auto-demotion**: Demote lessons with low usage or negative eval impact
6. **Cross-repo lessons**: Lessons that apply to multiple repos (scope: "global")

## FAQ

### Q: Why not auto-promote lessons?

**A:** Auto-promotion risks reinforcing bad patterns. A plausible-sounding but
incorrect lesson could degrade performance across many runs. Requiring explicit
approval creates a quality gate.

### Q: Can agents query candidates?

**A:** Yes, by setting `status=candidate` in the query. But the default is
`status=promoted` so agents don't see unreviewed content unless explicitly
configured to do so.

### Q: How do I migrate lessons between environments?

**A:** Copy the `.factory-runs/memory/` directory, or export/import via JSON:

```bash
# Export promoted lessons
factory memory list promoted > lessons-export.json

# Import on another system
cat lessons-export.json | jq -c '.[]' | while read lesson; do
  factory memory propose --text "$(echo $lesson | jq -r .text)" ...
done
```

### Q: What if a promoted lesson becomes outdated?

**A:** For now, reject it manually or delete the file. Future: deprecation
workflow with expiry dates for promoted lessons.

### Q: Can lessons include code snippets?

**A:** Yes, short snippets are fine (within the 10k character limit). But
lessons should be principles and reminders, not executable scripts. Use
runbooks or tool definitions for executable code.

---

**Related docs:**
- [`IMPLEMENTATION_PLAN.md §7`](IMPLEMENTATION_PLAN.md) — Memory and knowledge architecture
- [`EVALS.md`](EVALS.md) — Eval suite for measuring lesson impact
- [`docs/adr/0009-cli-first-external-tools.md`](adr/0009-cli-first-external-tools.md) — Why CLI-first, not MCP
