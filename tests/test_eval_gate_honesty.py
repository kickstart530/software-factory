"""The eval gate either evaluates a change or blocks; it never passes silently."""

import shutil

import yaml

from evals import ConfigVariant, EvalGate, load_golden_tasks
from evals.replay import ReplayResult
from factory.schemas import TaskStatus
from tests.conftest import REPO_ROOT


def _variant(tmp_path, name, edit=None):
    agents = tmp_path / name / "agents"
    shutil.copytree(REPO_ROOT / "agents", agents)
    models = tmp_path / name / "models.yaml"
    shutil.copy(REPO_ROOT / "config" / "models.yaml", models)
    if edit:
        edit(agents, models)
    return ConfigVariant(name=name, agents_dir=agents, models_config=models)


def _gate(tmp_path, candidate_edit):
    baseline = _variant(tmp_path, "baseline")
    candidate = _variant(tmp_path, "candidate", candidate_edit)
    tasks = load_golden_tasks(REPO_ROOT / "evals" / "golden")
    return EvalGate(baseline, candidate, tasks, tmp_path / "ws", tmp_path / "art").run()


def _edit_implementer(agents, change):
    path = agents / "implementer.yaml"
    spec = yaml.safe_load(path.read_text())
    change(spec)
    path.write_text(yaml.safe_dump(spec))


def test_identical_configs_pass(tmp_path):
    comparison = _gate(tmp_path, None)
    assert not comparison.blocked and comparison.candidate_passed == comparison.baseline_passed


def test_budget_change_is_replayed_and_catches_regressions(tmp_path):
    def tighten(agents, models):
        _edit_implementer(agents, lambda s: s["budget"].update(turns=1))

    comparison = _gate(tmp_path, tighten)
    assert comparison.has_regressions and comparison.blocked
    assert comparison.inconclusive == []


def test_prompt_change_blocks_as_inconclusive(tmp_path):
    def reword(agents, models):
        (agents / "prompts" / "implementer.md").write_text("A different prompt.\n")

    comparison = _gate(tmp_path, reword)
    assert not comparison.has_regressions and comparison.blocked
    assert any("system prompt for 'implementer'" in r for r in comparison.inconclusive)


def test_model_routing_change_blocks_as_inconclusive(tmp_path):
    def reroute(agents, models):
        models.write_text(models.read_text().replace("effort: high", "effort: max", 1))

    comparison = _gate(tmp_path, reroute)
    assert comparison.blocked and any("model routing" in r for r in comparison.inconclusive)


def test_tool_change_blocks_as_inconclusive(tmp_path):
    def drop_tool(agents, models):
        _edit_implementer(agents, lambda s: s["tools"].remove("search_code"))

    comparison = _gate(tmp_path, drop_tool)
    assert comparison.blocked and any(
        "tools for 'implementer'" in r for r in comparison.inconclusive
    )


def test_missing_candidate_result_is_a_regression(tmp_path):
    def result(task_id, passed):
        return ReplayResult(task_id=task_id, description=task_id, passed=passed,
                            actual_status=TaskStatus.SUCCEEDED,
                            expected_status=TaskStatus.SUCCEEDED, gates_passed=[],
                            gates_failed=[], expected_gates=[], usage={})  # fmt: skip

    gate = EvalGate(None, None, [], tmp_path, tmp_path)
    comparison = gate._compare_results([result("a", True)], [])
    assert comparison.regressions and "no candidate result" in comparison.regressions[0]
