"""Sandbox pod spawning for ephemeral agent task execution.

This module provides the infrastructure for spawning isolated Kubernetes pods
(AD-2: one task = one ephemeral sandbox) with gVisor runtime, egress control,
and resource quotas.
"""

from factory.sandbox.config import EgressPolicy, SandboxConfig, SandboxImage
from factory.sandbox.spawner import SandboxResult, SandboxSpawner

__all__ = [
    "SandboxConfig",
    "SandboxImage",
    "EgressPolicy",
    "SandboxSpawner",
    "SandboxResult",
]
