"""Sandbox pod spawning for ephemeral agent task execution.

This module provides the infrastructure for spawning isolated Kubernetes pods
(AD-2: one task = one ephemeral sandbox) with gVisor runtime, egress control,
and resource quotas.
"""

from factory.sandbox.config import SandboxConfig, SandboxImage, EgressPolicy
from factory.sandbox.spawner import SandboxSpawner, SandboxResult

__all__ = [
    "SandboxConfig",
    "SandboxImage",
    "EgressPolicy",
    "SandboxSpawner",
    "SandboxResult",
]
