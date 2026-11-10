"""Protected paths: reads are free, writes need the risk tier, and the diff is the judge."""

import pytest

from factory.harness.policy import DEFAULT_PROTECTED_PATHS as PROTECTED
from factory.harness.policy import CallContext, RulePolicy, protected_path_violations
from factory.harness.tools import builtin_tools
from factory.schemas import RiskTier, TaskStatus
from tests.conftest import make_task, reply, submit, tool_call

RUN = {t.name: t for t in builtin_tools()}["run_command"]
FIX = tool_call("edit_file", path="app.py", old="return a - b", new="return a + b")


def _check(cmd, tier=RiskTier.LOW):
    ctx = CallContext(agent="implementer", risk_tier=tier, tainted=False)
    return RulePolicy().check(RUN, {"command": cmd}, ctx).allowed


@pytest.mark.parametrize(
    "cmd",
    ["cat Dockerfile", "ls infra", "grep -r migrations docs", "git log -- .github/workflows",
     "cat .factory/gates.yaml", "pytest -q 2>&1 | tail -5", "cp a.py factory/cli.py"],
)  # fmt: skip
def test_reads_and_unrelated_writes_are_allowed(cmd):
    assert _check(cmd)


@pytest.mark.parametrize(
    "cmd",
    ["echo x > Dockerfile", "printf g >> .factory/gates.yaml", "rm -rf infra",
     "sed -i s/a/b/ .github/workflows/ci.yml", "git rm migrations/001.sql"],
)  # fmt: skip
def test_writes_to_protected_paths_are_denied_below_high(cmd):
    assert not _check(cmd)
    assert _check(cmd, RiskTier.HIGH)


def test_violations_helper():
    paths = ["app.py", "infra/main.tf", ".factory/gates.yaml", "src/Dockerfile"]
    assert len(protected_path_violations(paths, RiskTier.LOW, PROTECTED)) == 3
    assert protected_path_violations(paths, RiskTier.HIGH, PROTECTED) == []


def _sneaky_write(path):
    # Evades the command check on purpose: no redirect or write tool in the text.
    return tool_call(
        "run_command",
        command=f"python3 -c \"import pathlib; pathlib.Path('{path}').write_text('x')\"",
    )


def test_submit_rejected_when_diff_touches_protected_path(build):
    h, model, _ = build([reply(_sneaky_write("Dockerfile"), FIX), reply(submit()), reply(submit())])
    result = h.run(make_task())
    assert result.status == TaskStatus.FAILED
    gates = {g.name: g for g in result.gates}
    assert (
        not gates["protected_paths"].passed and "Dockerfile" in gates["protected_paths"].output_tail
    )
    assert gates["unit_tests"].passed  # only the path rule failed


def test_high_risk_task_may_change_protected_paths(build):
    h, _, _ = build(
        [reply(_sneaky_write("Dockerfile"), FIX), reply(submit())], risk_tier=RiskTier.HIGH
    )
    assert h.run(make_task()).status == TaskStatus.SUCCEEDED
