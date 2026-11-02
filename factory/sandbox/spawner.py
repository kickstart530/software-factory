"""Sandbox pod spawner for Temporal activities."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from factory.sandbox.config import SandboxConfig
from factory.schemas import Task, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of a sandbox pod execution."""

    task_id: str
    pod_name: str
    exit_code: int
    logs: str
    duration_seconds: float
    error: str | None = None


class SandboxSpawner:
    """Spawns ephemeral Kubernetes pods for agent task execution.
    
    This is a thin interface to Kubernetes for sandbox spawning. It does not
    own cluster IaC or worker deployment manifests. The Temporal activity
    calls this to spawn sandboxes without owning the cluster infrastructure.
    
    Design principles:
    - CLI-first: no MCP, no tool gateway dependencies
    - Thin interface: minimal coupling to Temporal
    - One task = one ephemeral pod (AD-2)
    - Isolation via gVisor runtime class
    - Egress control via NetworkPolicy
    """

    def __init__(self, config: SandboxConfig | None = None):
        """Initialize the spawner.
        
        Args:
            config: Sandbox configuration. If None, loads from environment.
        """
        self.config = config or SandboxConfig.from_env()
        self._k8s_client: Any = None  # Lazy-loaded kubernetes client

    def _ensure_k8s_client(self) -> Any:
        """Lazy-load the Kubernetes client."""
        if self._k8s_client is not None:
            return self._k8s_client

        try:
            from kubernetes import client, config  # type: ignore

            # Try in-cluster config first, fall back to kubeconfig
            try:
                config.load_incluster_config()
                logger.info("Loaded in-cluster Kubernetes configuration")
            except config.config_exception.ConfigException:
                config.load_kube_config()
                logger.info("Loaded Kubernetes configuration from kubeconfig")

            self._k8s_client = client
            return self._k8s_client

        except ImportError as e:
            raise RuntimeError(
                "kubernetes package not installed. Install with: pip install kubernetes"
            ) from e

    async def spawn_and_wait(
        self, task: Task, heartbeat_callback: callable | None = None
    ) -> SandboxResult:
        """Spawn a sandbox pod, wait for completion, and return the result.
        
        Args:
            task: The task to execute in the sandbox
            heartbeat_callback: Optional callback to invoke periodically (for Temporal heartbeats)
            
        Returns:
            SandboxResult with execution outcome
            
        Raises:
            RuntimeError: If pod creation or execution fails
        """
        k8s = self._ensure_k8s_client()
        core_v1 = k8s.CoreV1Api()

        # Generate pod name from task ID (must be DNS-1123 compliant)
        pod_name = self._pod_name_for_task(task)

        logger.info(f"Spawning sandbox pod {pod_name} for task {task.id}")

        # Create the pod manifest
        pod_manifest = self._create_pod_manifest(task, pod_name)

        start_time = time.time()

        try:
            # Create the pod
            await asyncio.to_thread(
                core_v1.create_namespaced_pod,
                namespace=self.config.namespace,
                body=pod_manifest,
            )
            logger.info(f"Pod {pod_name} created in namespace {self.config.namespace}")

            # Wait for the pod to complete, with periodic heartbeats
            result = await self._wait_for_completion(
                core_v1, pod_name, heartbeat_callback
            )

            # Fetch logs
            try:
                logs = await asyncio.to_thread(
                    core_v1.read_namespaced_pod_log,
                    name=pod_name,
                    namespace=self.config.namespace,
                    tail_lines=10000,  # Last 10k lines
                )
            except Exception as e:
                logger.warning(f"Failed to fetch logs for pod {pod_name}: {e}")
                logs = f"(log fetch failed: {e})"

            duration = time.time() - start_time

            # Delete the pod (cleanup)
            await self._delete_pod(core_v1, pod_name)

            return SandboxResult(
                task_id=task.id,
                pod_name=pod_name,
                exit_code=result["exit_code"],
                logs=logs,
                duration_seconds=duration,
                error=result.get("error"),
            )

        except Exception as e:
            logger.error(f"Sandbox spawn failed for task {task.id}: {e}")
            # Attempt cleanup
            try:
                await self._delete_pod(core_v1, pod_name)
            except Exception:
                pass  # Cleanup failure is not critical

            raise RuntimeError(f"Sandbox spawn failed: {e}") from e

    def _pod_name_for_task(self, task: Task) -> str:
        """Generate a Kubernetes-compliant pod name from a task ID.
        
        DNS-1123: lowercase alphanumeric, '-' or '.', start/end with alphanumeric,
        max 253 chars.
        """
        # Sanitize task ID: replace non-compliant chars with '-'
        import re

        sanitized = re.sub(r"[^a-z0-9.-]", "-", task.id.lower())
        # Ensure starts with alphanumeric
        if not sanitized[0].isalnum():
            sanitized = "t" + sanitized
        # Truncate if needed (reserve space for suffix if needed)
        max_len = 63  # Pod name limit
        if len(sanitized) > max_len:
            sanitized = sanitized[:max_len]
        return sanitized.rstrip("-.")

    def _create_pod_manifest(self, task: Task, pod_name: str) -> dict[str, Any]:
        """Create a Kubernetes pod manifest for the task.
        
        Security considerations:
        - Clean HOME: no host credential mounts (~/.ssh, ~/.config, ~/.netrc)
        - Explicit credential injection via Secrets Broker (future)
        - Read-only root filesystem (where practical)
        - Writable workspace only in /workspace
        - No host PATH pollution
        """
        # Build environment variables for the task
        # CRITICAL: Clean environment, no host HOME or credential paths
        env_vars = [
            {"name": "TASK_ID", "value": task.id},
            {"name": "WORK_ORDER_ID", "value": task.work_order_id},
            {"name": "AGENT_NAME", "value": task.agent},
            {"name": "TASK_TITLE", "value": task.title},
            # Clean HOME inside the sandbox
            {"name": "HOME", "value": "/home/sandbox"},
            # Minimal PATH (no host pollution)
            {"name": "PATH", "value": "/usr/local/bin:/usr/bin:/bin"},
            # Disable automatic credential helpers
            {"name": "GIT_TERMINAL_PROMPT", "value": "0"},
            # TODO: Pass repo URL, branch, credentials (via Secrets Broker) when available
        ]

        # Volume mounts: writable workspace, read-only everywhere else
        volume_mounts = [
            {
                "name": "workspace",
                "mountPath": "/workspace",
            },
            {
                "name": "home",
                "mountPath": "/home/sandbox",
            },
        ]

        volumes = [
            {
                "name": "workspace",
                "emptyDir": {"sizeLimit": self.config.resources.ephemeral_storage_limit},
            },
            {
                "name": "home",
                "emptyDir": {"sizeLimit": "1Gi"},
            },
        ]

        # Security context: non-root, no privilege escalation
        security_context = {
            "runAsNonRoot": True,
            "runAsUser": 1000,  # sandbox user
            "runAsGroup": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": False,  # TODO: Enable when possible
            "capabilities": {"drop": ["ALL"]},
        }

        # Container spec
        container = {
            "name": "agent-harness",
            "image": self.config.image.full_name,
            "imagePullPolicy": self.config.image.pull_policy,
            "env": env_vars,
            "volumeMounts": volume_mounts,
            "securityContext": security_context,
            "resources": {
                "requests": {
                    "cpu": self.config.resources.cpu_request,
                    "memory": self.config.resources.memory_request,
                },
                "limits": {
                    "cpu": self.config.resources.cpu_limit,
                    "memory": self.config.resources.memory_limit,
                    "ephemeral-storage": self.config.resources.ephemeral_storage_limit,
                },
            },
            "workingDir": "/workspace",
        }

        # Pod security context
        pod_security_context = {
            "fsGroup": 1000,
            "seccompProfile": {"type": "RuntimeDefault"},
        }

        # Pod spec
        pod_spec: dict[str, Any] = {
            "containers": [container],
            "volumes": volumes,
            "restartPolicy": "Never",  # One-shot execution
            "serviceAccountName": self.config.service_account,
            "securityContext": pod_security_context,
        }

        # Add runtime class for isolation (gVisor)
        if self.config.runtime_class:
            pod_spec["runtimeClassName"] = self.config.runtime_class

        # Pod manifest
        manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self.config.namespace,
                "labels": {
                    **self.config.labels,
                    "task-id": task.id[:63],  # Label value limit
                    "agent": task.agent,
                    "work-order-id": task.work_order_id[:63],
                },
                "annotations": {
                    "task.instructions": task.instructions[:4096],  # Truncate if needed
                },
            },
            "spec": pod_spec,
        }

        return manifest

    async def _wait_for_completion(
        self, core_v1: Any, pod_name: str, heartbeat_callback: callable | None
    ) -> dict[str, Any]:
        """Poll the pod until it reaches a terminal state.
        
        Returns:
            Dict with 'exit_code' and optional 'error'
        """
        timeout = self.config.timeout_seconds
        poll_interval = 5  # seconds
        elapsed = 0

        while elapsed < timeout:
            # Send heartbeat if provided
            if heartbeat_callback:
                try:
                    heartbeat_callback()
                except Exception as e:
                    logger.warning(f"Heartbeat callback failed: {e}")

            # Check pod status
            try:
                pod = await asyncio.to_thread(
                    core_v1.read_namespaced_pod_status,
                    name=pod_name,
                    namespace=self.config.namespace,
                )
            except Exception as e:
                return {"exit_code": -1, "error": f"Failed to read pod status: {e}"}

            phase = pod.status.phase

            if phase == "Succeeded":
                return {"exit_code": 0}
            elif phase == "Failed":
                # Extract failure reason
                error = "Pod failed"
                if pod.status.container_statuses:
                    cs = pod.status.container_statuses[0]
                    if cs.state.terminated:
                        exit_code = cs.state.terminated.exit_code
                        reason = cs.state.terminated.reason or "unknown"
                        error = f"Container exited with code {exit_code}: {reason}"
                        return {"exit_code": exit_code, "error": error}
                return {"exit_code": 1, "error": error}

            # Still running or pending
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout reached
        return {"exit_code": -1, "error": f"Pod execution timeout ({timeout}s)"}

    async def _delete_pod(self, core_v1: Any, pod_name: str) -> None:
        """Delete a pod (cleanup)."""
        try:
            await asyncio.to_thread(
                core_v1.delete_namespaced_pod,
                name=pod_name,
                namespace=self.config.namespace,
            )
            logger.info(f"Deleted pod {pod_name}")
        except Exception as e:
            logger.warning(f"Failed to delete pod {pod_name}: {e}")


async def run_task_in_sandbox(
    task: Task,
    spawner: SandboxSpawner | None = None,
    heartbeat_callback: callable | None = None,
) -> TaskResult:
    """High-level function to run a task in a sandbox and return a TaskResult.
    
    This is the main entry point for the Temporal activity. It:
    1. Spawns a sandbox pod
    2. Waits for completion with heartbeats
    3. Converts the sandbox result to a TaskResult
    
    Args:
        task: The task to execute
        spawner: Optional SandboxSpawner (creates one if not provided)
        heartbeat_callback: Optional callback for Temporal heartbeats
        
    Returns:
        TaskResult with execution outcome
    """
    spawner = spawner or SandboxSpawner()

    try:
        sandbox_result = await spawner.spawn_and_wait(task, heartbeat_callback)

        # Convert to TaskResult
        # In production, the sandbox would write a TaskResult JSON to stdout/file
        # For now, we create a minimal result based on exit code
        if sandbox_result.exit_code == 0:
            status = TaskStatus.SUCCEEDED
            summary = f"Task completed successfully in {sandbox_result.duration_seconds:.1f}s"
            error = None
        else:
            status = TaskStatus.FAILED
            summary = (
                f"Task failed with exit code {sandbox_result.exit_code} "
                f"after {sandbox_result.duration_seconds:.1f}s"
            )
            error = sandbox_result.error or f"Exit code {sandbox_result.exit_code}"

        return TaskResult(
            task_id=task.id,
            status=status,
            summary=summary,
            error=error,
        )

    except Exception as e:
        logger.error(f"Sandbox execution failed for task {task.id}: {e}")
        return TaskResult(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary="Sandbox execution failed",
            error=str(e),
        )
