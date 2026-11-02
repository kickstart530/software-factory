"""Evaluation harness: golden tasks, replay runner, eval gating, and scorecards."""

from evals.gate import (
    ConfigVariant,
    EvalGate,
    GateComparison,
    print_gate_report,
    save_gate_report,
)
from evals.replay import ReplayRunner, load_golden_tasks

__all__ = [
    "ConfigVariant",
    "EvalGate",
    "GateComparison",
    "ReplayRunner",
    "load_golden_tasks",
    "print_gate_report",
    "save_gate_report",
]
