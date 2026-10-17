import threading

from factory.orchestrator import Supervisor
from factory.schemas import TaskDAG, TaskResult, TaskStatus
from tests.conftest import make_task


def dag(*specs):
    return TaskDAG(
        work_order_id="WO1",
        tasks=[make_task(id=i, depends_on=list(d)) for i, d in specs],
    )


def test_runs_in_dependency_order_with_parallelism():
    order, lock = [], threading.Lock()

    def run(task):
        with lock:
            order.append(task.id)
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)

    d = dag(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"]))
    results = Supervisor(run, max_parallel=2).execute(d)
    assert all(r.status == TaskStatus.SUCCEEDED for r in results.values())
    assert order[0] == "a" and order[-1] == "d" and set(order[1:3]) == {"b", "c"}


def test_failure_cancels_downstream_but_not_siblings():
    def run(task):
        status = TaskStatus.FAILED if task.id == "b" else TaskStatus.SUCCEEDED
        return TaskResult(task_id=task.id, status=status)

    d = dag(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b"]), ("e", ["d"]))
    results = Supervisor(run).execute(d)
    assert results["c"].status == TaskStatus.SUCCEEDED
    assert results["d"].status == TaskStatus.CANCELLED
    assert results["e"].status == TaskStatus.CANCELLED


def test_crashing_runner_fails_only_its_task():
    def run(task):
        if task.id == "a":
            raise RuntimeError("sandbox lost")
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)

    results = Supervisor(run).execute(dag(("a", []), ("b", [])))
    assert results["a"].status == TaskStatus.FAILED and "sandbox lost" in results["a"].error
    assert results["b"].status == TaskStatus.SUCCEEDED


def test_escalated_task_blocks_dependents_without_cancelling():
    def run(task):
        status = TaskStatus.ESCALATED if task.id == "a" else TaskStatus.SUCCEEDED
        return TaskResult(task_id=task.id, status=status)

    d = dag(("a", []), ("b", ["a"]))
    results = Supervisor(run).execute(d)
    assert results["a"].status == TaskStatus.ESCALATED
    assert "b" not in results and d.by_id()["b"].status == TaskStatus.PENDING
