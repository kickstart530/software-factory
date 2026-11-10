"""Temporal TaskDAG orchestration, run against Temporal's time-skipping test server.

The server binary is downloaded on first use; if it can't be fetched (no network),
the workflow tests skip rather than fail.
"""

import asyncio
import threading
import time
import uuid

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

from factory.control import KillSwitch
from factory.control.kill_switch import FileKillSwitchStore
from factory.schemas import TaskDAG, TaskResult, TaskStatus
from factory.temporal_supervisor import (
    ExecuteTaskDAGWorkflow,
    cancel_unreachable,
    create_temporal_worker,
)
from tests.conftest import make_task

QUEUE = "test-factory-tasks"


def dag(*specs):
    return TaskDAG(
        work_order_id="WO1",
        tasks=[make_task(id=i, depends_on=list(d)) for i, d in specs],
    )


class Runner:
    """Records start order and concurrency; fails, escalates or raises on request."""

    def __init__(self, fail=(), escalate=(), crash=(), delay=0.0):
        self.fail, self.escalate, self.crash = set(fail), set(escalate), set(crash)
        self.delay = delay
        self.order: list[str] = []
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, task):
        with self._lock:
            self.order.append(task.id)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            if task.id in self.crash:
                raise RuntimeError("sandbox lost")
            status = (
                TaskStatus.FAILED
                if task.id in self.fail
                else TaskStatus.ESCALATED
                if task.id in self.escalate
                else TaskStatus.SUCCEEDED
            )
            return TaskResult(task_id=task.id, status=status)
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture(scope="module")
def env():
    loop = asyncio.new_event_loop()
    try:
        environment = loop.run_until_complete(WorkflowEnvironment.start_time_skipping())
    except RuntimeError as e:  # test server download blocked or unavailable
        loop.close()
        pytest.skip(f"Temporal test server unavailable: {e}")
    yield loop, environment
    loop.run_until_complete(environment.shutdown())
    loop.close()


def execute(env, the_dag, runner, max_parallel=4, kill_switch=None):
    loop, environment = env

    async def go():
        client: Client = environment.client
        worker = await create_temporal_worker(client, QUEUE, runner, kill_switch=kill_switch)
        async with worker:
            out = await client.execute_workflow(
                ExecuteTaskDAGWorkflow.run,
                args=[the_dag.model_dump(mode="json"), max_parallel],
                id=f"wf-{uuid.uuid4()}",
                task_queue=QUEUE,
            )
        return {tid: TaskResult.model_validate(r) for tid, r in out.items()}

    return loop.run_until_complete(asyncio.wait_for(go(), timeout=120))


def test_runs_in_dependency_order_and_completes(env):
    runner = Runner()
    results = execute(env, dag(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])), runner)
    assert {t: r.status for t, r in results.items()} == dict.fromkeys("abcd", TaskStatus.SUCCEEDED)
    assert runner.order[0] == "a" and runner.order[-1] == "d"
    assert set(runner.order[1:3]) == {"b", "c"}


def test_independent_tasks_run_in_parallel_up_to_limit(env):
    runner = Runner(delay=0.3)
    execute(env, dag(*[(f"t{i}", []) for i in range(5)]), runner, max_parallel=2)
    assert runner.peak == 2 and len(runner.order) == 5


def test_failure_cancels_downstream_but_not_siblings(env):
    the_dag = dag(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b"]), ("e", ["d"]))
    results = execute(env, the_dag, Runner(fail={"b"}))
    assert results["b"].status == TaskStatus.FAILED
    assert results["c"].status == TaskStatus.SUCCEEDED
    assert results["d"].status == results["e"].status == TaskStatus.CANCELLED


def test_crashing_runner_fails_only_its_task(env):
    results = execute(env, dag(("a", []), ("b", [])), Runner(crash={"a"}))
    assert results["a"].status == TaskStatus.FAILED and "sandbox lost" in results["a"].error
    assert results["b"].status == TaskStatus.SUCCEEDED


def test_escalated_task_leaves_dependents_pending_and_workflow_returns(env):
    results = execute(env, dag(("a", []), ("b", ["a"])), Runner(escalate={"a"}))
    assert results["a"].status == TaskStatus.ESCALATED
    assert "b" not in results  # neither run nor cancelled


def test_async_runner_gets_heartbeat_callback(env):
    beats = []

    async def runner(task, heartbeat_callback=None):
        heartbeat_callback()
        beats.append(task.id)
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)

    results = execute(env, dag(("a", [])), runner)
    assert results["a"].status == TaskStatus.SUCCEEDED and beats == ["a"]


def test_kill_switch_holds_tasks_until_resumed(env, tmp_path):
    ks = KillSwitch(FileKillSwitchStore(tmp_path / "ks"))
    ks.pause(reason="incident")
    runner = Runner()
    loop, _ = env
    # Resume shortly after the workflow sees the pause (time-skipping makes
    # the workflow's recheck sleep instant once the worker is idle).
    timer = threading.Timer(1.0, ks.resume)
    timer.start()
    try:
        results = execute(env, dag(("a", []), ("b", ["a"])), runner, kill_switch=ks)
    finally:
        timer.cancel()
    assert runner.order == ["a", "b"]
    assert all(r.status == TaskStatus.SUCCEEDED for r in results.values())


def test_invalid_dag_fails_the_workflow(env):
    loop, environment = env
    bad = {"work_order_id": "WO1", "tasks": []}

    async def go():
        worker = await create_temporal_worker(environment.client, QUEUE, Runner())
        async with worker:
            await environment.client.execute_workflow(
                ExecuteTaskDAGWorkflow.run, args=[bad, 4], id=f"wf-{uuid.uuid4()}", task_queue=QUEUE
            )

    with pytest.raises(WorkflowFailureError):
        loop.run_until_complete(asyncio.wait_for(go(), timeout=60))


def test_cancel_unreachable_is_transitive():
    the_dag = dag(("a", []), ("b", ["a"]), ("c", ["b"]))
    tasks = the_dag.by_id()
    tasks["a"].status = TaskStatus.FAILED
    results = {}
    cancel_unreachable(tasks, results)
    assert tasks["b"].status == tasks["c"].status == TaskStatus.CANCELLED
    assert set(results) == {"b", "c"}
