"""Approval requirements fail closed; the kill switch stops work resumably."""

from factory.control import KillSwitch
from factory.control.kill_switch import FileKillSwitchStore
from factory.harness.policy_engine import EnhancedPolicy
from factory.orchestrator import Supervisor
from factory.schemas import TaskDAG, TaskResult, TaskStatus
from tests.conftest import make_task, reply, submit, tool_call

FIX = tool_call("edit_file", path="app.py", old="return a - b", new="return a + b")


def test_operation_requiring_approval_is_denied_not_run(build, workspace):
    h, model, tracer = build([reply(FIX), reply(submit()), reply(submit())])
    h.deps.policy = EnhancedPolicy(approval_required_tools={"edit_file"})
    result = h.run(make_task())
    assert "a - b" in (workspace / "app.py").read_text()  # the edit never ran
    denial = model.requests[1].messages[-1]["content"][0]
    assert denial["is_error"] and "Human approval is required" in denial["content"]
    assert tracer.named("policy.denied")[0]["rule"] == "approval-required"
    assert result.status == TaskStatus.FAILED  # gate still fails: the fix was refused


def test_kill_switch_stops_run_and_resume_continues(build, tmp_path):
    ks = KillSwitch(FileKillSwitchStore(tmp_path / "ks"))
    h, model, tracer = build([reply(FIX), reply(submit())])
    h.deps.kill_switch = ks
    ks.pause(reason="incident")
    task = make_task()
    paused = h.run(task)
    assert paused.status == TaskStatus.CANCELLED and "kill switch" in paused.error
    assert model.requests == []  # no model call while paused

    ks.resume()
    assert h.run(task).status == TaskStatus.SUCCEEDED  # not stuck on the paused result


def test_tenant_pause_only_affects_that_tenant(build, tmp_path):
    ks = KillSwitch(FileKillSwitchStore(tmp_path / "ks"))
    ks.pause(tenant_id="acme", reason="over budget")
    h, _, _ = build([reply(FIX), reply(submit())])
    h.deps.kill_switch = ks
    assert h.run(make_task(tenant_id="other")).status == TaskStatus.SUCCEEDED


def test_supervisor_does_not_schedule_while_paused_and_resumes_later(tmp_path):
    ks = KillSwitch(FileKillSwitchStore(tmp_path / "ks"))
    ran = []

    def run(task):
        ran.append(task.id)
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)

    dag = TaskDAG(
        work_order_id="WO1",
        tasks=[make_task(id="a"), make_task(id="b", depends_on=["a"])],
    )
    ks.pause(reason="incident")
    assert Supervisor(run, kill_switch=ks).execute(dag) == {}
    assert ran == [] and all(t.status == TaskStatus.PENDING for t in dag.tasks)

    ks.resume()
    results = Supervisor(run, kill_switch=ks).execute(dag)
    assert ran == ["a", "b"] and results["b"].status == TaskStatus.SUCCEEDED


def test_supervisor_returns_paused_task_to_pending(tmp_path):
    ks = KillSwitch(FileKillSwitchStore(tmp_path / "ks"))

    def run(task):  # the harness noticed the pause mid-task
        ks.pause(reason="incident")
        return TaskResult(task_id=task.id, status=TaskStatus.CANCELLED, error="paused")

    dag = TaskDAG(
        work_order_id="WO1", tasks=[make_task(id="a"), make_task(id="b", depends_on=["a"])]
    )
    results = Supervisor(run, kill_switch=ks).execute(dag)
    assert results == {}
    assert [t.status for t in dag.tasks] == [TaskStatus.PENDING, TaskStatus.PENDING]
