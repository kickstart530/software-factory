# ADR-0002: One task runs in one ephemeral sandbox

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Agents execute arbitrary shell commands against untrusted repositories and dependencies.

## Decision

Each task gets a fresh sandbox (gVisor or Firecracker runtime class on Kubernetes) with a read-only root filesystem, a writable workspace holding the clone, CPU/memory/disk quotas, default-deny egress through an allowlist proxy, and short-lived credentials scoped to the task. Sandboxes are discarded when the task ends; state that must survive lives in checkpoints and git.

## Consequences

+ Bounded blast radius; clean state per task; safe to use spot capacity.
- Cold-start cost per task (mitigated by pre-built language images and a warm pool).
- The harness must checkpoint so a lost sandbox costs at most one turn.
