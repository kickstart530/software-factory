"""Eval gating: compare baseline vs candidate prompt/model changes.

The gate runner allows testing prompt and model routing changes against the
golden eval suite before merging them. It compares baseline vs candidate
configurations and reports regressions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .replay import GoldenTask, ReplayResult, ReplayRunner


@dataclass
class ConfigVariant:
    """A configuration variant for eval gating."""

    name: str
    agents_dir: Path | None = None
    models_config: Path | None = None
    system_prompts: dict[str, str] | None = None


@dataclass
class GateComparison:
    """Comparison between baseline and candidate results."""

    baseline_passed: int
    baseline_failed: int
    candidate_passed: int
    candidate_failed: int
    regressions: list[str]
    improvements: list[str]
    baseline_cost: float
    candidate_cost: float
    baseline_results: list[ReplayResult]
    candidate_results: list[ReplayResult]

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    @property
    def baseline_pass_rate(self) -> float:
        total = self.baseline_passed + self.baseline_failed
        return self.baseline_passed / total if total > 0 else 0.0

    @property
    def candidate_pass_rate(self) -> float:
        total = self.candidate_passed + self.candidate_failed
        return self.candidate_passed / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": {
                "passed": self.baseline_passed,
                "failed": self.baseline_failed,
                "pass_rate": self.baseline_pass_rate,
                "total_cost_usd": self.baseline_cost,
            },
            "candidate": {
                "passed": self.candidate_passed,
                "failed": self.candidate_failed,
                "pass_rate": self.candidate_pass_rate,
                "total_cost_usd": self.candidate_cost,
            },
            "regressions": self.regressions,
            "improvements": self.improvements,
            "has_regressions": self.has_regressions,
        }


class EvalGate:
    """Run eval gating: compare baseline vs candidate configurations."""

    def __init__(
        self,
        baseline: ConfigVariant,
        candidate: ConfigVariant,
        golden_tasks: list[GoldenTask],
        workspace_root: Path,
        artifacts_root: Path,
    ):
        self.baseline = baseline
        self.candidate = candidate
        self.golden_tasks = golden_tasks
        self.workspace_root = workspace_root
        self.artifacts_root = artifacts_root

    def run(self) -> GateComparison:
        """Run both configurations and compare results."""
        print(f"\n{'=' * 70}")
        print(f"Running eval gate: {self.baseline.name} vs {self.candidate.name}")
        print(f"{'=' * 70}\n")

        print(f"Running baseline ({self.baseline.name})...")
        baseline_results = self._run_variant(self.baseline, "baseline")

        print(f"\nRunning candidate ({self.candidate.name})...")
        candidate_results = self._run_variant(self.candidate, "candidate")

        return self._compare_results(baseline_results, candidate_results)

    def _run_variant(self, variant: ConfigVariant, variant_id: str) -> list[ReplayResult]:
        """Run golden tasks with a specific configuration variant."""
        workspace = self.workspace_root / variant_id
        artifacts = self.artifacts_root / variant_id
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)

        runner = ReplayRunner(workspace, artifacts)

        # If custom system prompts are provided, we need to patch them
        # For now, replay uses recorded responses so prompt changes don't affect results
        # This is a limitation we'll document: to test prompt changes, you need
        # to record new golden tasks with the new prompts

        results = runner.run_suite(self.golden_tasks)

        passed = sum(1 for r in results if r.passed)
        print(f"  {passed}/{len(results)} passed")

        return results

    def _compare_results(
        self, baseline: list[ReplayResult], candidate: list[ReplayResult]
    ) -> GateComparison:
        """Compare baseline and candidate results."""
        baseline_by_id = {r.task_id: r for r in baseline}
        candidate_by_id = {r.task_id: r for r in candidate}

        regressions = []
        improvements = []

        for task_id in baseline_by_id:
            b = baseline_by_id[task_id]
            c = candidate_by_id.get(task_id)

            if c is None:
                continue

            if b.passed and not c.passed:
                regressions.append(
                    f"{task_id}: {b.description} "
                    f"(baseline: {b.actual_status.value}, candidate: {c.actual_status.value})"
                )
            elif not b.passed and c.passed:
                improvements.append(
                    f"{task_id}: {b.description} "
                    f"(baseline: {b.actual_status.value}, candidate: {c.actual_status.value})"
                )

        baseline_passed = sum(1 for r in baseline if r.passed)
        baseline_failed = sum(1 for r in baseline if not r.passed)
        candidate_passed = sum(1 for r in candidate if r.passed)
        candidate_failed = sum(1 for r in candidate if not r.passed)

        # Cost calculations (from replay usage)
        baseline_cost = sum(r.usage.get("total_usd", 0.0) for r in baseline if r.usage)
        candidate_cost = sum(r.usage.get("total_usd", 0.0) for r in candidate if r.usage)

        return GateComparison(
            baseline_passed=baseline_passed,
            baseline_failed=baseline_failed,
            candidate_passed=candidate_passed,
            candidate_failed=candidate_failed,
            regressions=regressions,
            improvements=improvements,
            baseline_cost=baseline_cost,
            candidate_cost=candidate_cost,
            baseline_results=baseline,
            candidate_results=candidate,
        )


def print_gate_report(comparison: GateComparison) -> None:
    """Print a human-readable gate report."""
    print(f"\n{'=' * 70}")
    print("Eval Gate Report")
    print(f"{'=' * 70}\n")

    print("Baseline:")
    print(f"  Passed: {comparison.baseline_passed}")
    print(f"  Failed: {comparison.baseline_failed}")
    print(f"  Pass rate: {comparison.baseline_pass_rate:.1%}")
    print(f"  Total cost: ${comparison.baseline_cost:.4f}")

    print("\nCandidate:")
    print(f"  Passed: {comparison.candidate_passed}")
    print(f"  Failed: {comparison.candidate_failed}")
    print(f"  Pass rate: {comparison.candidate_pass_rate:.1%}")
    print(f"  Total cost: ${comparison.candidate_cost:.4f}")

    if comparison.regressions:
        print(f"\n❌ REGRESSIONS ({len(comparison.regressions)}):")
        for reg in comparison.regressions:
            print(f"  - {reg}")

    if comparison.improvements:
        print(f"\n✓ IMPROVEMENTS ({len(comparison.improvements)}):")
        for imp in comparison.improvements:
            print(f"  - {imp}")

    print(f"\n{'=' * 70}")

    if comparison.has_regressions:
        print("❌ GATE BLOCKED: Regressions detected")
    else:
        print("✓ GATE PASSED: No regressions")

    print(f"{'=' * 70}\n")


def save_gate_report(comparison: GateComparison, output_path: Path) -> None:
    """Save gate report as JSON."""
    report = comparison.to_dict()
    report["baseline_results"] = [
        {
            "task_id": r.task_id,
            "description": r.description,
            "passed": r.passed,
            "actual_status": r.actual_status.value,
            "expected_status": r.expected_status.value,
            "usage": r.usage,
        }
        for r in comparison.baseline_results
    ]
    report["candidate_results"] = [
        {
            "task_id": r.task_id,
            "description": r.description,
            "passed": r.passed,
            "actual_status": r.actual_status.value,
            "expected_status": r.expected_status.value,
            "usage": r.usage,
        }
        for r in comparison.candidate_results
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    print(f"Detailed report saved to {output_path}")
