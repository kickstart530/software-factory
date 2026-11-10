"""Core Secrets Broker interface and abstractions."""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

SECRET_KEYS = ("_secret_value",)


def write_credential_state(
    path: os.PathLike[str] | str, credentials: dict[str, dict[str, Any]]
) -> None:
    """Persist credential metadata, never secret values, readable only by the owner."""
    public = {
        cid: {k: v for k, v in data.items() if k not in SECRET_KEYS}
        for cid, data in credentials.items()
    }
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(public, f, indent=2)
    os.replace(tmp, path)


def read_credential_state(path: os.PathLike[str] | str) -> tuple[dict[str, dict[str, Any]], bool]:
    """Load credential metadata. Returns (credentials, had_secrets_on_disk)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, False
    leaked = any(k in entry for entry in data.values() for k in SECRET_KEYS)
    for entry in data.values():
        for k in SECRET_KEYS:
            entry.pop(k, None)
    return data, leaked


class SecretStatus(StrEnum):
    """Lifecycle status of a minted credential."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MintRequest(BaseModel):
    """Request to mint a task-scoped credential."""

    task_id: str = Field(min_length=1, description="Task this credential is scoped to")
    work_order_id: str = Field(min_length=1)
    repo_url: str = Field(description="Repository URL this credential grants access to")
    branch: str | None = Field(default=None, description="Branch restriction if applicable")
    tool_name: str = Field(description="Tool that will use this credential (e.g. 'git', 'gh')")
    scopes: list[str] = Field(
        default_factory=list,
        description="Required scopes/permissions (e.g. ['repo', 'write:packages'])",
    )
    ttl_seconds: int = Field(
        default=3600, gt=0, le=86400, description="Time-to-live in seconds (max 24h)"
    )
    reason: str = Field(default="", description="Human-readable reason for audit trail")


class Credential(BaseModel):
    """A minted, task-scoped credential. Never includes the actual secret value."""

    credential_id: str = Field(description="Unique identifier for this credential")
    task_id: str
    work_order_id: str
    repo_url: str
    branch: str | None
    tool_name: str
    scopes: list[str]
    status: SecretStatus
    minted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    fingerprint: str = Field(
        description="Hash of the secret value for tracking without exposing it"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretsBroker(ABC):
    """Abstract interface for minting and managing short-lived credentials.

    Implementations must ensure:
    1. Credentials are scoped to exactly one task
    2. Secret values are NEVER returned in Credential objects
    3. Secret values are NEVER logged or stored in transcripts
    4. Credentials can be injected into tool execution environments (e.g. env vars)
    5. Expired credentials cannot be used
    """

    @abstractmethod
    def mint(self, request: MintRequest) -> Credential:
        """Mint a new short-lived credential.

        Args:
            request: Specification of the credential to create

        Returns:
            Credential metadata (WITHOUT the secret value)

        Raises:
            ValueError: If the request is invalid or cannot be fulfilled
        """
        pass

    @abstractmethod
    def get_credential(self, credential_id: str) -> Credential | None:
        """Retrieve credential metadata by ID.

        Args:
            credential_id: The credential to look up

        Returns:
            Credential metadata if found, None otherwise.
            Never includes the actual secret value.
        """
        pass

    @abstractmethod
    def inject_credential(self, credential_id: str, env: dict[str, str]) -> dict[str, str]:
        """Inject a credential into an environment dict for tool execution.

        This is the ONLY method that accesses the actual secret value.
        The secret is placed in the environment dict and never returned
        to the caller in any other form.

        Args:
            credential_id: The credential to inject
            env: Environment dict to modify (typically os.environ.copy())

        Returns:
            Modified environment dict with credential injected

        Raises:
            ValueError: If credential not found, expired, or revoked
        """
        pass

    @abstractmethod
    def revoke(self, credential_id: str) -> bool:
        """Immediately revoke a credential before its natural expiry.

        Args:
            credential_id: The credential to revoke

        Returns:
            True if revoked, False if not found or already revoked/expired
        """
        pass

    @abstractmethod
    def list_credentials(
        self, task_id: str | None = None, status: SecretStatus | None = None
    ) -> list[Credential]:
        """List credentials, optionally filtered by task and/or status.

        Args:
            task_id: If provided, only return credentials for this task
            status: If provided, only return credentials with this status

        Returns:
            List of credential metadata (without secret values)
        """
        pass

    @abstractmethod
    def cleanup_expired(self) -> int:
        """Remove expired credentials from storage.

        Returns:
            Number of credentials cleaned up
        """
        pass

    @staticmethod
    def compute_fingerprint(secret_value: str) -> str:
        """Compute a SHA-256 fingerprint of a secret value for tracking."""
        return hashlib.sha256(secret_value.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def is_expired(credential: Credential) -> bool:
        """Check if a credential has expired."""
        return datetime.now(UTC) >= credential.expires_at
