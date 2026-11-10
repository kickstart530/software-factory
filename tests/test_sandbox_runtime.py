"""Sandbox pods report the harness's TaskResult; the spawner never fakes success."""

import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from factory.cli import main
from factory.sandbox.spawner import (
    RESULT_MARKER,
    SandboxResult,
    SandboxSpawner,
    parse_task_result,
    run_task_in_sandbox,
)
from factory.schemas import TaskResult, TaskStatus
from tests.conftest import make_task


def _result_line(result):
    return RESULT_MARKER + result.model_dump_json()


def test_parse_task_result_takes_the_last_reported_result():
    first = TaskResult(task_id="T1", status=TaskStatus.FAILED)
    last = TaskResult(task_id="T1", status=TaskStatus.SUCCEEDED)
    logs = f"noise\n{_result_line(first)}\nmore\n{_result_line(last)}\n"
    assert parse_task_result(logs).status == TaskStatus.SUCCEEDED
    assert parse_task_result("no result here") is None
    assert parse_task_result(RESULT_MARKER + "{not json") is None


class FakeSpawner:
    def __init__(self, result):
        self.result = result

    async def spawn_and_wait(self, task, heartbeat_callback=None):
        return self.result


def _sandbox(exit_code=0, task_result=None):
    return SandboxResult(task_id="T1", pod_name="p", exit_code=exit_code, logs="",
                         duration_seconds=1.0, task_result=task_result)  # fmt: skip


def test_exit_zero_without_a_result_is_a_failure():
    out = asyncio.run(run_task_in_sandbox(make_task(), FakeSpawner(_sandbox(0))))
    assert out.status == TaskStatus.FAILED and "no TaskResult" in out.error


def test_reported_result_is_returned_as_is():
    reported = TaskResult(task_id="T1", status=TaskStatus.ESCALATED, summary="needs a human")
    out = asyncio.run(run_task_in_sandbox(make_task(), FakeSpawner(_sandbox(0, reported))))
    assert out == reported


def test_result_for_another_task_is_rejected():
    other = TaskResult(task_id="T2", status=TaskStatus.SUCCEEDED)
    out = asyncio.run(run_task_in_sandbox(make_task(), FakeSpawner(_sandbox(0, other))))
    assert out.status == TaskStatus.FAILED


class FakeCore:
    def __init__(self, create_error=None):
        self.create_error = create_error
        self.deleted = []

    def create_namespaced_pod(self, namespace, body):
        if self.create_error:
            raise self.create_error

    def read_namespaced_pod_status(self, name, namespace):
        return SimpleNamespace(status=SimpleNamespace(phase="Succeeded"))

    def read_namespaced_pod_log(self, name, namespace, tail_lines):
        return _result_line(TaskResult(task_id="T1", status=TaskStatus.SUCCEEDED))

    def delete_namespaced_pod(self, name, namespace):
        self.deleted.append(name)


def _spawner_with(core):
    spawner = SandboxSpawner()
    spawner._k8s_client = SimpleNamespace(CoreV1Api=lambda: core)
    return spawner


def test_failed_create_never_deletes_a_pod():
    core = FakeCore(create_error=RuntimeError("409 AlreadyExists"))
    with pytest.raises(RuntimeError, match="Sandbox spawn failed"):
        asyncio.run(_spawner_with(core).spawn_and_wait(make_task()))
    assert core.deleted == []


def test_successful_pod_is_parsed_and_deleted():
    core = FakeCore()
    result = asyncio.run(_spawner_with(core).spawn_and_wait(make_task()))
    assert result.task_result.status == TaskStatus.SUCCEEDED
    assert core.deleted == [result.pod_name]


# --- factory sandbox-entry --------------------------------------------------


@pytest.fixture
def origin(tmp_path, workspace):
    """A repo to clone, with a main branch."""
    subprocess.run(["git", "branch", "-M", "main"], cwd=workspace, check=True)
    return workspace


def _entry(monkeypatch, tmp_path, task, capsys):
    monkeypatch.setenv("FACTORY_TASK_JSON", task.model_dump_json())
    code = main(["sandbox-entry", "--workspace", str(tmp_path / "clone"),
                 "--state-dir", str(tmp_path / "state")])  # fmt: skip
    out = capsys.readouterr().out
    return code, parse_task_result(out)


def test_sandbox_entry_clones_branches_and_reports(monkeypatch, tmp_path, origin, capsys):
    import factory.cli as cli

    seen = {}

    class FakeHarness:
        def run(self, task):
            ws = tmp_path / "clone"
            seen["branch"] = subprocess.run(
                ["git", "branch", "--show-current"], cwd=ws, capture_output=True, text=True
            ).stdout.strip()
            seen["has_app"] = (ws / "app.py").is_file()
            # An agent command (minimal environment, no git identity vars) can commit.
            from factory.harness.tools import ToolContext, builtin_tools

            run = {t.name: t for t in builtin_tools()}["run_command"]
            ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")
            out = run.invoke(ctx, {"command": "echo x > new.txt && git add -A && git commit -qm c"})
            seen["commit_ok"] = not out.is_error
            return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary="done")

    monkeypatch.setattr(cli, "_harness", lambda args, task: FakeHarness())
    task = make_task(repo={"url": str(origin), "base_branch": "main"})
    code, result = _entry(monkeypatch, tmp_path, task, capsys)
    assert code == 0 and result.status == TaskStatus.SUCCEEDED
    assert seen == {"branch": "factory/WO1/T1", "has_app": True, "commit_ok": True}


def test_sandbox_entry_without_key_reports_failure(monkeypatch, tmp_path, origin, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from factory.harness import credentials

    credentials._reset_for_tests()
    task = make_task(repo={"url": str(origin), "base_branch": "main"})
    code, result = _entry(monkeypatch, tmp_path, task, capsys)
    assert code == 0 and result.status == TaskStatus.FAILED
    assert "ANTHROPIC_API_KEY" in result.error


def test_sandbox_entry_bad_repo_reports_failure(monkeypatch, tmp_path, capsys):
    task = make_task(repo={"url": str(tmp_path / "nope"), "base_branch": "main"})
    code, result = _entry(monkeypatch, tmp_path, task, capsys)
    assert result.status == TaskStatus.FAILED and "git clone" in result.error


def test_sandbox_entry_without_repo_reports_failure(monkeypatch, tmp_path, capsys):
    code, result = _entry(monkeypatch, tmp_path, make_task(), capsys)
    assert result.status == TaskStatus.FAILED and "no repo" in result.error


assert os.sep == "/"  # these tests use POSIX paths
