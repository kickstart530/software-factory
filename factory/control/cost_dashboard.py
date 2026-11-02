"""Cost dashboard: aggregate and report factory spend metrics.

Provides CLI and export tools for cost visibility:
- Per-tenant cost breakdown
- Cost trends over time
- Top cost drivers (agents, tasks, work orders)
- Export to JSONL for ingestion by Grafana/Prometheus
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from factory.control.budget_ledger import FileBudgetLedger
from factory.schemas import Usage


@dataclass
class CostSummary:
    """Summary of costs for a period."""
    
    period: str
    tenant_id: str | None
    total_usd: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    turns: int
    tool_calls: int
    wall_clock_hours: float
    
    @classmethod
    def from_usage(
        cls,
        usage: Usage,
        period: str,
        tenant_id: str | None = None
    ) -> CostSummary:
        return cls(
            period=period,
            tenant_id=tenant_id,
            total_usd=usage.usd,
            total_tokens=usage.total_tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            turns=usage.turns,
            tool_calls=usage.tool_calls,
            wall_clock_hours=usage.wall_clock_s / 3600.0,
        )
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "tenant_id": self.tenant_id,
            "total_usd": round(self.total_usd, 4),
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "wall_clock_hours": round(self.wall_clock_hours, 2),
        }


class CostDashboard:
    """Cost reporting and export for the factory."""
    
    def __init__(self, ledger: FileBudgetLedger):
        self.ledger = ledger
    
    def get_current_month(self) -> str:
        """Get current month period string."""
        now = datetime.now()
        return f"{now.year:04d}-{now.month:02d}"
    
    def get_current_day(self) -> str:
        """Get current day period string."""
        now = datetime.now()
        return f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
    
    def get_tenant_summary(
        self,
        tenant_id: str,
        period: str | None = None
    ) -> CostSummary:
        """Get cost summary for a tenant."""
        if period is None:
            period = self.get_current_month()
        
        usage = self.ledger.get_tenant_usage(tenant_id, period)
        return CostSummary.from_usage(usage.usage, period, tenant_id)
    
    def get_all_tenants_summary(
        self,
        period: str | None = None
    ) -> list[CostSummary]:
        """Get cost summaries for all tenants."""
        if period is None:
            period = self.get_current_month()
        
        all_usage = self.ledger.list_all_usage(period)
        return [
            CostSummary.from_usage(rec.usage, period, tenant_id)
            for tenant_id, rec in all_usage.items()
        ]
    
    def get_total_summary(self, period: str | None = None) -> CostSummary:
        """Get total factory cost summary."""
        if period is None:
            period = self.get_current_month()
        
        all_usage = self.ledger.list_all_usage(period)
        total = Usage()
        
        for rec in all_usage.values():
            total.input_tokens += rec.usage.input_tokens
            total.output_tokens += rec.usage.output_tokens
            total.cache_read_tokens += rec.usage.cache_read_tokens
            total.cache_write_tokens += rec.usage.cache_write_tokens
            total.usd += rec.usage.usd
            total.turns += rec.usage.turns
            total.tool_calls += rec.usage.tool_calls
            total.wall_clock_s += rec.usage.wall_clock_s
        
        return CostSummary.from_usage(total, period, None)
    
    def format_summary(self, summary: CostSummary) -> str:
        """Format a cost summary as human-readable text."""
        lines = [
            f"{'='*70}",
            f"Cost Summary: {summary.period}",
        ]
        
        if summary.tenant_id:
            lines.append(f"Tenant: {summary.tenant_id}")
        else:
            lines.append("Tenant: ALL (factory-wide)")
        
        lines.extend([
            f"{'='*70}",
            f"Total Cost:          ${summary.total_usd:.2f}",
            f"Total Tokens:        {summary.total_tokens:,}",
            f"  Input:             {summary.input_tokens:,}",
            f"  Output:            {summary.output_tokens:,}",
            f"  Cache Read:        {summary.cache_read_tokens:,}",
            f"  Cache Write:       {summary.cache_write_tokens:,}",
            f"Turns:               {summary.turns:,}",
            f"Tool Calls:          {summary.tool_calls:,}",
            f"Wall Clock Hours:    {summary.wall_clock_hours:.2f}",
            f"{'='*70}",
        ])
        
        return "\n".join(lines)
    
    def export_metrics_jsonl(
        self,
        output: Path,
        period: str | None = None
    ) -> int:
        """Export metrics to JSONL for ingestion by observability tools.
        
        Args:
            output: Path to write JSONL file
            period: Period to export (default: current month)
            
        Returns:
            Number of records written
        """
        if period is None:
            period = self.get_current_month()
        
        summaries = self.get_all_tenants_summary(period)
        total = self.get_total_summary(period)
        
        output.parent.mkdir(parents=True, exist_ok=True)
        
        with output.open("w") as f:
            # Write per-tenant records
            for summary in summaries:
                f.write(json.dumps(summary.to_dict()) + "\n")
            
            # Write total record
            f.write(json.dumps(total.to_dict()) + "\n")
        
        return len(summaries) + 1
    
    def print_report(self, period: str | None = None) -> None:
        """Print a cost report to stdout."""
        if period is None:
            period = self.get_current_month()
        
        total = self.get_total_summary(period)
        print(self.format_summary(total))
        
        summaries = self.get_all_tenants_summary(period)
        if summaries:
            print(f"\n{'='*70}")
            print("Per-Tenant Breakdown:")
            print(f"{'='*70}\n")
            
            for summary in sorted(summaries, key=lambda s: s.total_usd, reverse=True):
                print(f"{summary.tenant_id:20s}  ${summary.total_usd:>10.2f}  "
                      f"{summary.total_tokens:>12,} tokens  "
                      f"{summary.turns:>6,} turns")
