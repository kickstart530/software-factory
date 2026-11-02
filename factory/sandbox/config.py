"""Configuration for sandbox pod spawning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SandboxImage(BaseModel):
    """Container image configuration for sandbox pods."""

    repository: str = Field(
        default="software-factory/agent-harness",
        description="Image repository path",
    )
    tag: str = Field(
        default="latest",
        description="Image tag",
    )
    pull_policy: Literal["Always", "IfNotPresent", "Never"] = Field(
        default="IfNotPresent",
        description="Kubernetes image pull policy",
    )

    @property
    def full_name(self) -> str:
        """Return the full image name."""
        return f"{self.repository}:{self.tag}"


class EgressPolicy(BaseModel):
    """Egress network policy for sandbox pods."""

    mode: Literal["deny-all", "allowlist", "allow-all"] = Field(
        default="deny-all",
        description="Egress policy mode",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Allowed domains when mode is 'allowlist'",
    )
    allowed_cidrs: list[str] = Field(
        default_factory=list,
        description="Allowed CIDR ranges when mode is 'allowlist'",
    )

    @classmethod
    def default_factory(cls) -> EgressPolicy:
        """Create a default egress policy with common allowlist."""
        return cls(
            mode="allowlist",
            allowed_domains=[
                # Package registries
                "pypi.org",
                "files.pythonhosted.org",
                "registry.npmjs.org",
                # Git hosts
                "github.com",
                "gitlab.com",
                # Model gateway (will be internal in production)
                "api.anthropic.com",
            ],
        )


class ResourceQuota(BaseModel):
    """Resource limits for sandbox pods."""

    cpu_request: str = Field(default="1000m", description="CPU request (e.g., '1000m', '2')")
    cpu_limit: str = Field(default="2000m", description="CPU limit")
    memory_request: str = Field(default="2Gi", description="Memory request (e.g., '2Gi', '4Gi')")
    memory_limit: str = Field(default="4Gi", description="Memory limit")
    ephemeral_storage_limit: str = Field(
        default="10Gi", description="Ephemeral storage limit"
    )


class SandboxConfig(BaseModel):
    """Configuration for sandbox pod spawning."""

    namespace: str = Field(
        default="factory-sandboxes",
        description="Kubernetes namespace for sandbox pods",
    )
    runtime_class: str | None = Field(
        default="gvisor",
        description="Runtime class for isolation (gvisor, kata, or None for default)",
    )
    image: SandboxImage = Field(default_factory=SandboxImage)
    egress: EgressPolicy = Field(default_factory=EgressPolicy.default_factory)
    resources: ResourceQuota = Field(default_factory=ResourceQuota)
    timeout_seconds: int = Field(
        default=7200,  # 2 hours
        description="Maximum wall-clock time for a sandbox pod",
    )
    service_account: str = Field(
        default="factory-sandbox-runner",
        description="Kubernetes service account for sandbox pods",
    )
    labels: dict[str, str] = Field(
        default_factory=lambda: {"app": "factory-sandbox", "managed-by": "temporal"},
        description="Labels to apply to sandbox pods",
    )

    @classmethod
    def from_env(cls) -> SandboxConfig:
        """Create configuration from environment variables.
        
        Environment variables:
        - FACTORY_SANDBOX_NAMESPACE: Kubernetes namespace
        - FACTORY_SANDBOX_RUNTIME_CLASS: Runtime class name
        - FACTORY_SANDBOX_IMAGE: Image repository:tag
        - FACTORY_SANDBOX_EGRESS_MODE: Egress policy mode
        """
        import os

        config = cls()

        if ns := os.getenv("FACTORY_SANDBOX_NAMESPACE"):
            config.namespace = ns

        if runtime := os.getenv("FACTORY_SANDBOX_RUNTIME_CLASS"):
            config.runtime_class = runtime if runtime.lower() != "none" else None

        if image := os.getenv("FACTORY_SANDBOX_IMAGE"):
            if ":" in image:
                repo, tag = image.rsplit(":", 1)
                config.image.repository = repo
                config.image.tag = tag
            else:
                config.image.repository = image

        if egress_mode := os.getenv("FACTORY_SANDBOX_EGRESS_MODE"):
            config.egress.mode = egress_mode  # type: ignore

        return config
