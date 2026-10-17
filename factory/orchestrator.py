"""Supervisor: executes a TaskDAG by running ready tasks in parallel.

This is the in-process scheduler used for local runs and tests. Phase 2 moves
the same logic into Temporal workflows (AD-1) so it survives restarts; the
contract stays the same: a callable that runs one Task and returns a TaskResult.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from factory.harness.telemetry import NullTracer, Tracer
from factory.schemas import TaskDAG, TaskResult, TaskStatus

TaskRunner = Callable[..., TaskResult]


class Supervisor:
    def __init__(self, run_task: TaskRunner, max_parallel: int = 4, tracer: Tracer | None = None):
        self.run_task = run_task
        self.max_parallel = max_parallel
        self.tracer = tracer or NullTracer()

    def execute(self, dag: TaskDAG) -> dict[str, TaskResult]:
        """Run until every task is terminal or blocked. Mutates task statuses in ``dag``."""
        tasks = dag.by_id()
        results: dict[str, TaskResult] = {}
        running: dict[Future[TaskResult], str] = {}

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            while True:
                self._cancel_unreachable(dag, results)
                for task in dag.ready():
                    if len(running) >= self.max_parallel:
                        break
                    task.status = TaskStatus.RUNNING
                    self.tracer.event("task.started", task_id=task.id, agent=task.agent)
                    running[pool.submit(self.run_task, task)] = task.id
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    tid = running.pop(fut)
                    try:
                        result = fut.result()
                    except Exception as e:  # a crashed runner fails its task, not the DAG
                        result = TaskResult(task_id=tid, status=TaskStatus.FAILED, error=repr(e))
                    tasks[tid].status = result.status
                    results[tid] = result
        return results

    def _cancel_unreachable(self, dag: TaskDAG, results: dict[str, TaskResult]) -> None:
        """Cancel pending tasks downstream of a failed or cancelled task."""
        dead = {TaskStatus.FAILED, TaskStatus.CANCELLED}
        changed = True
        while changed:
            changed = False
            status = {t.id: t.status for t in dag.tasks}
            for t in dag.tasks:
                if t.status == TaskStatus.PENDING and any(status[d] in dead for d in t.depends_on):
                    t.status = TaskStatus.CANCELLED
                    results[t.id] = TaskResult(
                        task_id=t.id,
                        status=TaskStatus.CANCELLED,
                        error="an upstream task failed",
                    )
                    changed = True
