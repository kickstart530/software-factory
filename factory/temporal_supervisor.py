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
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from factory.schemas import Task, TaskDAG, TaskResult, TaskStatus

# Type alias for the task runner contract
# Can be sync or async; async runners receive heartbeat_callback kwarg
TaskRunner = Callable[[Task], TaskResult] | Callable[[Task, Any], TaskResult]

# Process-wide injection points, set by the worker before it starts polling.
_task_runner: TaskRunner | None = None
_kill_switch: Any | None = None  # factory.control.KillSwitch

HEARTBEAT_INTERVAL_S = 30
PAUSE_RECHECK = timedelta(minutes=1)
PAUSED_ERROR = "paused by kill switch"


def set_task_runner(runner: TaskRunner) -> None:
    """Set the TaskRunner the activity uses. Call once, before the worker runs."""
    global _task_runner
    _task_runner = runner


def set_kill_switch(kill_switch: Any | None) -> None:
    """Set the kill switch the activity checks before starting a task."""
    global _kill_switch
    _kill_switch = kill_switch


# --- Activities ---


@activity.defn(name="run_task")
async def run_task_activity(task_dict: dict[str, Any]) -> dict[str, Any]:
    """Run one task (normally in an ephemeral sandbox pod) and return its TaskResult.

    Heartbeats every HEARTBEAT_INTERVAL_S while the task runs, so the workflow's
    heartbeat timeout only fires when the worker is actually gone. A synchronous
    runner runs in a thread so it can't block those heartbeats.
    """
    task = Task.model_validate(task_dict)
    if _task_runner is None:
        raise RuntimeError(
            "TaskRunner not configured. Worker must call set_task_runner() during initialization."
        )
    if _kill_switch is not None and _kill_switch.is_paused(task.tenant_id):
        return TaskResult(
            task_id=task.id, status=TaskStatus.CANCELLED, error=PAUSED_ERROR
        ).model_dump(mode="json")

    def heartbeat() -> None:
        activity.heartbeat(f"running task {task.id}")

    if asyncio.iscoroutinefunction(_task_runner):
        work = asyncio.ensure_future(_task_runner(task, heartbeat_callback=heartbeat))
    else:
        work = asyncio.ensure_future(asyncio.to_thread(_task_runner, task))
    while True:
        done, _ = await asyncio.wait({work}, timeout=HEARTBEAT_INTERVAL_S)
        if done:
            break
        heartbeat()
    return work.result().model_dump(mode="json")


# --- Workflows ---


def cancel_unreachable(tasks: dict[str, Task], results: dict[str, TaskResult]) -> None:
    """Cancel pending tasks downstream of a failed or cancelled task."""
    dead = {TaskStatus.FAILED, TaskStatus.CANCELLED}
    changed = True
    while changed:
        changed = False
        for task in tasks.values():
            if task.status == TaskStatus.PENDING and any(
                tasks[dep].status in dead for dep in task.depends_on
            ):
                task.status = TaskStatus.CANCELLED
                results[task.id] = TaskResult(
                    task_id=task.id, status=TaskStatus.CANCELLED, error="an upstream task failed"
                )
                changed = True


def _describe(error: BaseException) -> str:
    """Temporal wraps activity errors; include the causes, which hold the real message."""
    parts = []
    while error is not None and len(parts) < 4:
        parts.append(str(error) or type(error).__name__)
        error = error.__cause__
    return ": ".join(parts)


@workflow.defn(name="execute_task_dag")
class ExecuteTaskDAGWorkflow:
    """Run a TaskDAG durably: ready tasks run in parallel as activities.

    Same semantics as orchestrator.Supervisor: a failed or cancelled task cancels
    its dependents; an escalated task leaves its dependents pending; the workflow
    returns once nothing is running and nothing more can start. Tasks stopped by
    the kill switch go back to pending and are retried after PAUSE_RECHECK.
    """

    @workflow.run
    async def run(
        self, dag_dict: dict[str, Any], max_parallel: int = 4
    ) -> dict[str, dict[str, Any]]:
        try:
            dag = TaskDAG.model_validate(dag_dict)
        except ValueError as e:
            # A bad DAG never becomes valid: fail the workflow instead of letting
            # Temporal retry the workflow task forever.
            raise ApplicationError(f"invalid TaskDAG: {e}", non_retryable=True) from e
        tasks = dag.by_id()
        results: dict[str, TaskResult] = {}
        running: dict[Any, str] = {}  # activity handle -> task id

        while True:
            cancel_unreachable(tasks, results)
            for task in dag.ready():
                if len(running) >= max_parallel:
                    break
                task.status = TaskStatus.RUNNING
                workflow.logger.info(f"Starting task {task.id} (agent: {task.agent})")
                handle = workflow.start_activity(
                    run_task_activity,
                    task.model_dump(mode="json"),
                    start_to_close_timeout=timedelta(hours=2),
                    heartbeat_timeout=timedelta(minutes=5),
                    # A task's own retries live in the harness; never re-run it blindly.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                running[handle] = task.id

            if not running:
                if any(t.status == TaskStatus.PENDING and t.id in self._paused for t in dag.tasks):
                    await workflow.sleep(PAUSE_RECHECK)
                    self._paused.clear()
                    continue
                break

            done, _ = await workflow.wait(list(running), return_when=asyncio.FIRST_COMPLETED)
            for handle in done:
                tid = running.pop(handle)
                try:
                    result = TaskResult.model_validate(handle.result())
                except Exception as e:  # activity failed or timed out: fail this task only
                    result = TaskResult(task_id=tid, status=TaskStatus.FAILED, error=_describe(e))
                if result.status == TaskStatus.CANCELLED and result.error == PAUSED_ERROR:
                    tasks[tid].status = TaskStatus.PENDING
                    self._paused.add(tid)
                    continue
                tasks[tid].status = result.status
                results[tid] = result

        return {tid: r.model_dump(mode="json") for tid, r in results.items()}

    def __init__(self) -> None:
        self._paused: set[str] = set()


# --- Worker and Client Utilities ---


async def create_temporal_worker(
    client: Client,
    task_queue: str,
    runner: TaskRunner,
    kill_switch: Any | None = None,
) -> Worker:
    """Create a Temporal worker configured to run TaskDAG workflows.

    Args:
        client: Temporal client connected to the server
        task_queue: Task queue name to poll
        runner: The TaskRunner callable that executes tasks (sync or async).
                For sandbox spawning, pass run_task_in_sandbox from factory.sandbox.

    Returns:
        Configured Worker instance (caller must call worker.run())
    """
    # Inject the runner into the registry for activities to access
    set_task_runner(runner)
    set_kill_switch(kill_switch)

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
