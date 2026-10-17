import pytest
from pydantic import ValidationError

from factory.schemas import (
    AcceptanceCriterion,
    Finding,
    ReviewReport,
    TaskDAG,
    TaskStatus,
    WorkOrder,
)
from tests.conftest import make_task


def dag(*specs):
    return TaskDAG(
        work_order_id="WO1",
        tasks=[make_task(id=i, depends_on=list(deps)) for i, deps in specs],
    )


def test_topological_order_and_ready():
    d = dag(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"]))
    assert d.topological_order() == ["a", "b", "c", "d"]
    assert [t.id for t in d.ready()] == ["a"]
    d.by_id()["a"].status = TaskStatus.SUCCEEDED
    assert [t.id for t in d.ready()] == ["b", "c"]


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        dag(("a", ["b"]), ("b", ["a"]))


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="unknown"):
        dag(("a", ["zzz"]))


def test_duplicate_ids_rejected():
    with pytest.raises(ValidationError, match="unique"):
        dag(("a", []), ("a", []))


def test_gate_criterion_must_name_gate():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(id="c1", description="tests pass", verification="gate")


def test_review_cannot_approve_with_blocking_findings():
    f = Finding(file="a.py", severity="blocking", category="correctness", summary="x")
    with pytest.raises(ValidationError, match="blocking"):
        ReviewReport(verdict="approve", summary="lgtm", findings=[f])


def test_work_order_requires_criteria():
    with pytest.raises(ValidationError):
        WorkOrder(
            id="WO1",
            title="t",
            description="d",
            repo={"url": "x"},
            acceptance_criteria=[],
            requested_by="me",
        )
