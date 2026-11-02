"""Secrets Broker: short-lived, task-scoped credential management.

The Secrets Broker mints temporary credentials for agents to access external
resources (git hosts, cloud APIs, etc.) without ever exposing the credentials
in model-visible tool results or transcripts.

Core principles (per IMPLEMENTATION_PLAN.md Phase 3):
- No standing credentials: tokens are minted per task, scoped to one repo/branch
- Short-lived: credentials expire with the task or after a configurable TTL
- Never in model context: credentials injected server-side for CLI tools only
- Redacted from transcripts: pre-storage hooks strip minted secrets

Architecture:
- SecretsBroker: abstract interface
- Backends: pluggable storage (EnvFile, Vault, etc.)
- Redaction: hooks that strip secrets from transcripts before storage
"""

from factory.secrets.broker import (
    Credential,
    MintRequest,
    SecretsBroker,
    SecretStatus,
)

__all__ = [
    "Credential",
    "MintRequest",
    "SecretsBroker",
    "SecretStatus",
]
