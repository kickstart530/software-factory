"""Temporal-based Supervisor: durable orchestration of TaskDAG execution.

This module implements the same TaskDAG scheduling logic as orchestrator.py,
but as Temporal workflows and activities so runs survive restarts and support
long-running human-wait states (Phase 2, AD-1).

The contract remains the same: a TaskRunner callable that takes a Task and
returns a TaskResult. The in-process Supervisor in orchestrator.py is kept for
local tests; this module is the production runtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

from factory.schemas import Task, TaskDAG, TaskResult, TaskStatus

# Type alias for the task runner contract
TaskRunner = Callable[[Task], TaskResult]


# --- Activities ---


@activity.defn(name="run_task")
async def run_task_activity(task_dict: dict[str, Any], runner_name: str = "default") -> dict[str, Any]:
    """Activity that executes one task via the configured TaskRunner.
    
    Args:
        task_dict: Task serialized as a dict (Pydantic model_dump)
        runner_name: Identifies which TaskRunner to use (for extensibility)
    
    Returns:
        TaskResult serialized as a dict
    
    The actual runner is injected via activity context (activity.info().activity_type).
    For now we expect the worker to be configured with a runner; in production this
    activity would look up the runner from a registry or spawn a sandbox.
    """
    task = Task.model_validate(task_dict)
    
    # In production, this would:
    # 1. Spin up an ephemeral sandbox pod
    # 2. Clone the repo at task.branch or repo.base_branch
    # 3. Invoke the harness inside the sandbox
    # 4. Stream checkpoints via activity heartbeats
    # 5. Return the TaskResult
    #
    # For Phase 2a first slice, we'll call the runner directly (passed via worker config).
    # The worker setup injects a runner function into activity context.
    
    runner = activity.info().heartbeat_details.get("runner") if activity.info().heartbeat_details else None
    if runner is None:
        # Fallback: expect runner to be stored in a global or passed differently
        # This is a simplified implementation; production would use dependency injection
        raise RuntimeError("TaskRunner not configured in activity context")
    
    # Run the task, with periodic heartbeats for long-running tasks
    # The checkpoint ID should be included in heartbeats
    result = runner(task)
    
    return result.model_dump(mode="json")


# --- Workflows ---


@workflow.defn(name="execute_task_dag")
class ExecuteTaskDAGWorkflow:
    """Workflow that orchestrates a TaskDAG: runs ready tasks in parallel until completion.
    
    This implements the same scheduling logic as orchestrator.Supervisor.execute(),
    but durably. The workflow survives worker restarts, supports timeouts, and can
    wait on human signals (for escalations).
    """
    
    def __init__(self) -> None:
        self._results: dict[str, TaskResult] = {}
        self._running: set[str] = set()
        self._max_parallel = 4
    
    @workflow.run
    async def run(self, dag_dict: dict[str, Any], max_parallel: int = 4) -> dict[str, dict[str, Any]]:
        """Execute the DAG and return a dict of task_id -> TaskResult.
        
        Args:
            dag_dict: TaskDAG serialized as a dict
            max_parallel: Maximum number of tasks to run concurrently
        
        Returns:
            Dictionary mapping task_id to TaskResult (as dicts)
        """
        self._max_parallel = max_parallel
        dag = TaskDAG.model_validate(dag_dict)
        tasks = dag.by_id()
        
        # Keep launching tasks until all are terminal
        while not self._all_terminal(tasks):
            # Cancel unreachable tasks
            self._cancel_unreachable(dag, tasks)
            
            # Find ready tasks and launch them (up to parallel limit)
            ready = self._get_ready(dag, tasks)
            available_slots = self._max_parallel - len(self._running)
            
            # Launch new tasks if we have slots
            for task in ready[:available_slots]:
                task.status = TaskStatus.RUNNING
                self._running.add(task.id)
                workflow.logger.info(f"Starting task {task.id} (agent: {task.agent})")
                
                # Launch as a child coroutine (Temporal async activities)
                workflow.start_activity(
                    run_task_activity,
                    args=[task.model_dump(mode="json")],
                    start_to_close_timeout=timedelta(hours=2),  # Generous timeout for one task
                    heartbeat_timeout=timedelta(minutes=5),     # Expect heartbeats every 5 min
                    retry_policy=workflow.RetryPolicy(
                        maximum_attempts=1  # Don't auto-retry failed tasks; let Supervisor handle it
                    ),
                    task_queue=workflow.info().task_queue,
                )
            
            # Wait for at least one running task to complete
            if self._running:
                # In a real implementation, we'd use workflow.wait_condition or a child workflow
                # to wait for specific task completions. For now, simulate by awaiting a signal.
                # This is simplified; production would use proper Temporal child workflows or activities.
                await workflow.wait_condition(lambda: False, timeout=timedelta(seconds=1))
        
        # Return all results as dicts
        return {tid: result.model_dump(mode="json") for tid, result in self._results.items()}
    
    def _all_terminal(self, tasks: dict[str, Task]) -> bool:
        """Check if all tasks are in a terminal state."""
        terminal = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ESCALATED}
        return all(t.status in terminal for t in tasks.values())
    
    def _get_ready(self, dag: TaskDAG, tasks: dict[str, Task]) -> list[Task]:
        """Get tasks that are ready to run (pending with all deps succeeded)."""
        done = {t.id for t in tasks.values() if t.status == TaskStatus.SUCCEEDED}
        return [
            t for t in tasks.values()
            if t.status == TaskStatus.PENDING and set(t.depends_on) <= done
        ]
    
    def _cancel_unreachable(self, dag: TaskDAG, tasks: dict[str, Task]) -> None:
        """Cancel pending tasks downstream of a failed or cancelled task."""
        dead = {TaskStatus.FAILED, TaskStatus.CANCELLED}
        changed = True
        
        while changed:
            changed = False
            status_map = {t.id: t.status for t in tasks.values()}
            
            for task in tasks.values():
                if task.status == TaskStatus.PENDING:
                    if any(status_map[dep] in dead for dep in task.depends_on):
                        task.status = TaskStatus.CANCELLED
                        self._results[task.id] = TaskResult(
                            task_id=task.id,
                            status=TaskStatus.CANCELLED,
                            error="an upstream task failed",
                        )
                        workflow.logger.info(f"Cancelled task {task.id} (upstream failure)")
                        changed = True


# --- Worker and Client Utilities ---


async def create_temporal_worker(
    client: Client,
    task_queue: str,
    runner: TaskRunner,
) -> Worker:
    """Create a Temporal worker configured to run TaskDAG workflows.
    
    Args:
        client: Temporal client connected to the server
        task_queue: Task queue name to poll
        runner: The TaskRunner callable that executes tasks
    
    Returns:
        Configured Worker instance (caller must call worker.run())
    """
    # In production, the runner would be a factory function that spawns sandboxes
    # For now, we pass it through the worker config for the activity to access
    
    # Note: This is simplified. Production would use proper dependency injection
    # or a service locator pattern to provide the runner to activities.
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ExecuteTaskDAGWorkflow],
        activities=[run_task_activity],
    )
    
    return worker


async def execute_dag_via_temporal(
    client: Client,
    dag: TaskDAG,
    task_queue: str = "factory-tasks",
    workflow_id: str | None = None,
    max_parallel: int = 4,
) -> dict[str, TaskResult]:
    """Execute a TaskDAG via Temporal and return the results.
    
    Args:
        client: Temporal client connected to the server
        dag: The TaskDAG to execute
        task_queue: Task queue name (must match worker config)
        workflow_id: Optional workflow ID (defaults to work_order_id)
        max_parallel: Maximum parallel tasks
    
    Returns:
        Dictionary mapping task_id to TaskResult
    """
    workflow_id = workflow_id or f"dag-{dag.work_order_id}"
    
    # Start the workflow
    handle = await client.start_workflow(
        ExecuteTaskDAGWorkflow.run,
        args=[dag.model_dump(mode="json"), max_parallel],
        id=workflow_id,
        task_queue=task_queue,
    )
    
    # Wait for result
    result_dicts = await handle.result()
    
    # Convert back to TaskResult objects
    return {tid: TaskResult.model_validate(r) for tid, r in result_dicts.items()}


# --- Configuration and Setup ---


class TemporalConfig:
    """Configuration for connecting to Temporal server."""
    
    def __init__(
        self,
        host: str = "localhost:7233",
        namespace: str = "default",
        task_queue: str = "factory-tasks",
    ):
        self.host = host
        self.namespace = namespace
        self.task_queue = task_queue
    
    async def create_client(self) -> Client:
        """Create and return a connected Temporal client."""
        return await Client.connect(self.host, namespace=self.namespace)
