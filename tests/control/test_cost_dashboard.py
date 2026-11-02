"""Tests for cost dashboard and reporting."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from factory.control.budget_ledger import FileBudgetLedger
from factory.control.cost_dashboard import CostDashboard, CostSummary
from factory.schemas import Usage


@pytest.fixture
def ledger():
    with TemporaryDirectory() as tmpdir:
        yield FileBudgetLedger(Path(tmpdir))


@pytest.fixture
def dashboard(ledger):
    return CostDashboard(ledger)


def test_cost_summary_from_usage():
    """CostSummary should correctly aggregate usage."""
    usage = Usage(
        input_tokens=10000,
        output_tokens=5000,
        cache_read_tokens=2000,
        cache_write_tokens=500,
        usd=1.25,
        turns=20,
        tool_calls=30,
        wall_clock_s=3600.0
    )
    
    summary = CostSummary.from_usage(usage, "2024-03", "acme-corp")
    
    assert summary.period == "2024-03"
    assert summary.tenant_id == "acme-corp"
    assert summary.total_usd == 1.25
    assert summary.total_tokens == 17500  # 10k + 5k + 2k + 0.5k
    assert summary.input_tokens == 10000
    assert summary.output_tokens == 5000
    assert summary.turns == 20
    assert summary.tool_calls == 30
    assert summary.wall_clock_hours == 1.0


def test_get_tenant_summary_empty(dashboard, ledger):
    """Summary for tenant with no usage should be zero."""
    summary = dashboard.get_tenant_summary("new-tenant")
    
    assert summary.tenant_id == "new-tenant"
    assert summary.total_usd == 0.0
    assert summary.total_tokens == 0


def test_get_tenant_summary_with_usage(dashboard, ledger):
    """Summary should reflect recorded usage."""
    usage = Usage(
        input_tokens=10000,
        output_tokens=5000,
        usd=1.50
    )
    
    ledger.record_usage("acme-corp", usage, "T-1")
    
    summary = dashboard.get_tenant_summary("acme-corp")
    
    assert summary.tenant_id == "acme-corp"
    assert summary.total_usd == 1.50
    assert summary.total_tokens == 15000


def test_get_all_tenants_summary(dashboard, ledger):
    """Should return summaries for all tenants."""
    ledger.record_usage("tenant-a", Usage(usd=10.0, input_tokens=50000), "T-1")
    ledger.record_usage("tenant-b", Usage(usd=20.0, input_tokens=100000), "T-2")
    ledger.record_usage("tenant-c", Usage(usd=30.0, input_tokens=150000), "T-3")
    
    summaries = dashboard.get_all_tenants_summary()
    
    assert len(summaries) == 3
    
    tenants = {s.tenant_id: s for s in summaries}
    assert tenants["tenant-a"].total_usd == 10.0
    assert tenants["tenant-b"].total_usd == 20.0
    assert tenants["tenant-c"].total_usd == 30.0


def test_get_total_summary(dashboard, ledger):
    """Total summary should aggregate all tenants."""
    ledger.record_usage("tenant-a", Usage(usd=10.0, input_tokens=50000), "T-1")
    ledger.record_usage("tenant-b", Usage(usd=20.0, input_tokens=100000), "T-2")
    
    total = dashboard.get_total_summary()
    
    assert total.tenant_id is None  # Factory-wide
    assert total.total_usd == 30.0
    assert total.total_tokens == 150000


def test_format_summary(dashboard, ledger):
    """Summary formatting should be human-readable."""
    usage = Usage(
        input_tokens=10000,
        output_tokens=5000,
        usd=1.25,
        turns=20,
        tool_calls=30
    )
    ledger.record_usage("acme-corp", usage, "T-1")
    
    summary = dashboard.get_tenant_summary("acme-corp")
    formatted = dashboard.format_summary(summary)
    
    assert "acme-corp" in formatted
    assert "$1.25" in formatted
    assert "10,000" in formatted  # Input tokens
    assert "5,000" in formatted   # Output tokens
    assert "20" in formatted      # Turns


def test_export_metrics_jsonl(dashboard, ledger):
    """JSONL export should create valid output."""
    ledger.record_usage("tenant-a", Usage(usd=10.0, input_tokens=50000), "T-1")
    ledger.record_usage("tenant-b", Usage(usd=20.0, input_tokens=100000), "T-2")
    
    with TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "metrics.jsonl"
        count = dashboard.export_metrics_jsonl(output)
        
        assert count == 3  # 2 tenants + 1 total
        assert output.exists()
        
        # Parse JSONL
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 3
        
        records = [json.loads(line) for line in lines]
        
        # Check tenant records
        tenant_records = [r for r in records if r["tenant_id"] is not None]
        assert len(tenant_records) == 2
        
        # Check total record
        total_records = [r for r in records if r["tenant_id"] is None]
        assert len(total_records) == 1
        assert total_records[0]["total_usd"] == 30.0


def test_export_specific_period(dashboard, ledger):
    """Should be able to export a specific period."""
    # Record usage (will be in current period)
    ledger.record_usage("tenant-a", Usage(usd=10.0), "T-1")
    
    with TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "metrics.jsonl"
        
        # Export current month explicitly
        period = dashboard.get_current_month()
        count = dashboard.export_metrics_jsonl(output, period)
        
        assert count > 0
        assert output.exists()


def test_cost_summary_to_dict(dashboard):
    """Summary dict should have correct structure."""
    usage = Usage(
        input_tokens=10000,
        output_tokens=5000,
        usd=1.25,
        turns=20,
        tool_calls=30,
        wall_clock_s=3600.0
    )
    
    summary = CostSummary.from_usage(usage, "2024-03", "acme-corp")
    d = summary.to_dict()
    
    assert d["period"] == "2024-03"
    assert d["tenant_id"] == "acme-corp"
    assert d["total_usd"] == 1.25
    assert d["total_tokens"] == 15000
    assert d["turns"] == 20
    assert d["wall_clock_hours"] == 1.0
    
    # Should be JSON serializable
    json.dumps(d)


def test_multiple_tasks_aggregate(dashboard, ledger):
    """Multiple tasks should aggregate correctly."""
    for i in range(5):
        usage = Usage(usd=1.0, input_tokens=1000)
        ledger.record_usage("acme-corp", usage, f"T-{i}")
    
    summary = dashboard.get_tenant_summary("acme-corp")
    
    assert summary.total_usd == 5.0
    assert summary.total_tokens == 5000


def test_print_report_no_crash(dashboard, ledger, capsys):
    """Print report should not crash with empty or populated data."""
    # Empty report
    dashboard.print_report()
    captured = capsys.readouterr()
    assert "Cost Summary" in captured.out
    
    # With data
    ledger.record_usage("tenant-a", Usage(usd=10.0), "T-1")
    dashboard.print_report()
    captured = capsys.readouterr()
    assert "$10.00" in captured.out
    assert "tenant-a" in captured.out
