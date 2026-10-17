"""Budget enforcement: soft limits warn the agent, hard limits stop the run."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from factory.schemas import Budget, Usage


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class BudgetState(StrEnum):
    OK = "ok"
    SOFT = "soft"
    EXCEEDED = "exceeded"


class BudgetTracker:
    def __init__(self, budget: Budget, usage: Usage | None = None, clock=time.monotonic):
        self.budget = budget
        self.usage = usage.model_copy() if usage else Usage()
        self._clock = clock
        self._started = clock() - self.usage.wall_clock_s
        self._warned = False

    def record_model_call(
        self,
        price: Price | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        u = self.usage
        u.input_tokens += input_tokens
        u.output_tokens += output_tokens
        u.cache_read_tokens += cache_read_tokens
        u.cache_write_tokens += cache_write_tokens
        u.turns += 1
        if price:
            u.usd += (
                input_tokens * price.input
                + output_tokens * price.output
                + cache_read_tokens * price.cache_read
                + cache_write_tokens * price.cache_write
            ) / 1_000_000

    def record_tool_call(self) -> None:
        self.usage.tool_calls += 1

    def _ratios(self) -> dict[str, float]:
        b, u = self.budget, self.usage
        u.wall_clock_s = self._clock() - self._started
        pairs = {
            "tokens": (u.total_tokens, b.tokens),
            "usd": (u.usd, b.usd),
            "wall_clock_s": (u.wall_clock_s, b.wall_clock_s),
            "turns": (u.turns, b.turns),
            "tool_calls": (u.tool_calls, b.tool_calls),
        }
        return {k: used / cap for k, (used, cap) in pairs.items() if cap}

    def state(self) -> tuple[BudgetState, str | None]:
        ratios = self._ratios()
        if not ratios:
            return BudgetState.OK, None
        name, worst = max(ratios.items(), key=lambda kv: kv[1])
        if worst >= 1:
            return BudgetState.EXCEEDED, name
        if worst >= self.budget.soft_limit_ratio:
            return BudgetState.SOFT, name
        return BudgetState.OK, None

    def soft_warning(self) -> str | None:
        """A one-time nudge for the agent once any dimension crosses the soft limit."""
        state, dim = self.state()
        if state is BudgetState.SOFT and not self._warned:
            self._warned = True
            return (
                f"Budget notice: {dim} is above {int(self.budget.soft_limit_ratio * 100)}% of its "
                "limit. Converge: finish the smallest correct change, run the gates, and call "
                "submit_result. If you cannot finish, call escalate."
            )
        return None
