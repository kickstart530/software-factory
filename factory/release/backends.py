"""Deployment backends for canary orchestration."""

from __future__ import annotations

from typing import Protocol


class DeployBackend(Protocol):
    """Protocol for deployment backends that execute canary traffic shifts and rollbacks."""

    def deploy_canary(self, version: str, traffic_percent: int) -> None:
        """Deploy a version as canary with the specified traffic percentage.

        Args:
            version: Version/revision identifier to deploy.
            traffic_percent: Percentage of traffic to route to the canary (0-100).

        Raises:
            Exception: If deployment fails.
        """
        ...

    def rollback(self, version: str) -> None:
        """Rollback to the specified version.

        Args:
            version: Version/revision to rollback to.

        Raises:
            Exception: If rollback fails.
        """
        ...

    def get_metric(self, metric_name: str) -> float:
        """Get the current value of a metric for health checks.

        Args:
            metric_name: Name of the metric to query.

        Returns:
            Current metric value.

        Raises:
            Exception: If metric cannot be retrieved.
        """
        ...

    def get_current_version(self) -> str:
        """Get the currently deployed version.

        Returns:
            Current version identifier.
        """
        ...


class FakeBackend:
    """Fake deployment backend for testing."""

    def __init__(self) -> None:
        """Initialize fake backend with default state."""
        self.current_version = "v1.0.0"
        self.canary_version: str | None = None
        self.canary_traffic_percent = 0
        self.metrics: dict[str, float] = {
            "error_rate": 0.01,
            "latency_p99": 250.0,
            "latency_p50": 50.0,
            "success_rate": 0.99,
        }
        self.deploy_calls: list[tuple[str, int]] = []
        self.rollback_calls: list[str] = []

    def deploy_canary(self, version: str, traffic_percent: int) -> None:
        """Record canary deployment."""
        self.deploy_calls.append((version, traffic_percent))
        self.canary_version = version
        self.canary_traffic_percent = traffic_percent

    def rollback(self, version: str) -> None:
        """Record rollback."""
        self.rollback_calls.append(version)
        self.current_version = version
        self.canary_version = None
        self.canary_traffic_percent = 0

    def get_metric(self, metric_name: str) -> float:
        """Return configured metric value."""
        if metric_name not in self.metrics:
            raise ValueError(f"unknown metric: {metric_name}")
        return self.metrics[metric_name]

    def get_current_version(self) -> str:
        """Return current version."""
        return self.current_version

    def set_metric(self, metric_name: str, value: float) -> None:
        """Set a metric value for testing."""
        self.metrics[metric_name] = value

    def simulate_healthy(self) -> None:
        """Set metrics to healthy values."""
        self.metrics["error_rate"] = 0.005
        self.metrics["latency_p99"] = 200.0
        self.metrics["success_rate"] = 0.995

    def simulate_unhealthy(self) -> None:
        """Set metrics to unhealthy values."""
        self.metrics["error_rate"] = 0.15
        self.metrics["latency_p99"] = 5000.0
        self.metrics["success_rate"] = 0.85
