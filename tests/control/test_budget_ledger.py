"""Tests for multi-tenant budget ledger."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from factory.control.budget_ledger import (
    FileBudgetLedger,
    TenantBudgetConfig,
)
from factory.schemas import Usage


@pytest.fixture
def ledger():
    with TemporaryDirectory() as tmpdir:
        yield FileBudgetLedger(Path(tmpdir))


def test_default_tenant_config(ledger):
    """Default config should have no limits."""
    config = ledger.get_tenant_config("new-tenant")

    assert config.tenant_id == "new-tenant"
    assert config.monthly_usd_limit is None
    assert config.daily_usd_limit is None
    assert config.tokens_per_task_limit is None
    assert config.enabled is True


def test_set_tenant_config(ledger):
    """Should be able to set and retrieve tenant config."""
    config = TenantBudgetConfig(
        tenant_id="acme-corp",
        monthly_usd_limit=500.0,
        daily_usd_limit=50.0,
        tokens_per_task_limit=100000,
        enabled=True,
    )

    ledger.set_tenant_config(config)

    retrieved = ledger.get_tenant_config("acme-corp")
    assert retrieved.monthly_usd_limit == 500.0
    assert retrieved.daily_usd_limit == 50.0
    assert retrieved.tokens_per_task_limit == 100000


def test_record_usage(ledger):
    """Recording usage should update tenant records."""
    usage = Usage(
        input_tokens=5000, output_tokens=1500, usd=0.25, turns=10, tool_calls=5, wall_clock_s=120.0
    )

    ledger.record_usage("acme-corp", usage, "T-123")

    # Should update current month
    period = ledger._get_period(daily=False)
    monthly = ledger.get_tenant_usage("acme-corp", period)

    assert monthly.tenant_id == "acme-corp"
    assert monthly.period == period
    assert monthly.usage.input_tokens == 5000
    assert monthly.usage.output_tokens == 1500
    assert monthly.usage.usd == 0.25


def test_usage_accumulates(ledger):
    """Multiple usage records should accumulate."""
    usage1 = Usage(input_tokens=1000, output_tokens=500, usd=0.10)
    usage2 = Usage(input_tokens=2000, output_tokens=800, usd=0.15)

    ledger.record_usage("acme-corp", usage1, "T-1")
    ledger.record_usage("acme-corp", usage2, "T-2")

    period = ledger._get_period(daily=False)
    monthly = ledger.get_tenant_usage("acme-corp", period)

    assert monthly.usage.input_tokens == 3000
    assert monthly.usage.output_tokens == 1300
    assert monthly.usage.usd == 0.25


def test_check_quota_no_limits(ledger):
    """With no limits, quota should always pass."""
    usage = Usage(usd=1000.0, input_tokens=1000000)

    allowed, reason = ledger.check_quota("unlimited-tenant", usage)
    assert allowed
    assert reason is None


def test_check_quota_within_monthly_limit(ledger):
    """Usage within monthly limit should be allowed."""
    config = TenantBudgetConfig(tenant_id="acme-corp", monthly_usd_limit=100.0)
    ledger.set_tenant_config(config)

    # Record some existing usage
    ledger.record_usage("acme-corp", Usage(usd=50.0), "T-1")

    # Check if we can spend $25 more (should be allowed)
    allowed, reason = ledger.check_quota("acme-corp", Usage(usd=25.0))
    assert allowed
    assert reason is None


def test_check_quota_exceed_monthly_limit(ledger):
    """Usage exceeding monthly limit should be denied."""
    config = TenantBudgetConfig(tenant_id="acme-corp", monthly_usd_limit=100.0)
    ledger.set_tenant_config(config)

    # Record existing usage near limit
    ledger.record_usage("acme-corp", Usage(usd=90.0), "T-1")

    # Try to spend $20 more (should be denied)
    allowed, reason = ledger.check_quota("acme-corp", Usage(usd=20.0))
    assert not allowed
    assert "monthly budget" in reason.lower()


def test_check_quota_daily_limit(ledger):
    """Daily limit should be enforced separately from monthly."""
    config = TenantBudgetConfig(
        tenant_id="acme-corp", monthly_usd_limit=1000.0, daily_usd_limit=50.0
    )
    ledger.set_tenant_config(config)

    # Record today's usage
    ledger.record_usage("acme-corp", Usage(usd=45.0), "T-1")

    # Try to spend $10 more today (should exceed daily limit)
    allowed, reason = ledger.check_quota("acme-corp", Usage(usd=10.0))
    assert not allowed
    assert "daily budget" in reason.lower()


def test_check_quota_tokens_per_task(ledger):
    """Per-task token limit should be enforced."""
    config = TenantBudgetConfig(tenant_id="acme-corp", tokens_per_task_limit=10000)
    ledger.set_tenant_config(config)

    # Try to use more tokens than allowed
    usage = Usage(input_tokens=8000, output_tokens=5000)  # 13000 total
    allowed, reason = ledger.check_quota("acme-corp", usage)

    assert not allowed
    assert "10000" in reason


def test_check_quota_disabled_tenant(ledger):
    """Disabled tenant should be denied."""
    config = TenantBudgetConfig(tenant_id="acme-corp", enabled=False)
    ledger.set_tenant_config(config)

    allowed, reason = ledger.check_quota("acme-corp", Usage(usd=1.0))
    assert not allowed
    assert "disabled" in reason.lower()


def test_list_all_usage(ledger):
    """Should list usage for all tenants."""
    # Record usage for multiple tenants
    ledger.record_usage("tenant-a", Usage(usd=10.0), "T-1")
    ledger.record_usage("tenant-b", Usage(usd=20.0), "T-2")
    ledger.record_usage("tenant-c", Usage(usd=30.0), "T-3")

    period = ledger._get_period(daily=False)
    all_usage = ledger.list_all_usage(period)

    assert "tenant-a" in all_usage
    assert "tenant-b" in all_usage
    assert "tenant-c" in all_usage

    assert all_usage["tenant-a"].usage.usd == 10.0
    assert all_usage["tenant-b"].usage.usd == 20.0
    assert all_usage["tenant-c"].usage.usd == 30.0


def test_separate_tenants_isolated(ledger):
    """Tenant budgets should be isolated."""
    config_a = TenantBudgetConfig(tenant_id="tenant-a", monthly_usd_limit=100.0)
    config_b = TenantBudgetConfig(tenant_id="tenant-b", monthly_usd_limit=200.0)

    ledger.set_tenant_config(config_a)
    ledger.set_tenant_config(config_b)

    # Tenant A uses up their budget
    ledger.record_usage("tenant-a", Usage(usd=95.0), "T-1")

    # Tenant A should be blocked
    allowed_a, _ = ledger.check_quota("tenant-a", Usage(usd=10.0))
    assert not allowed_a

    # Tenant B should still be OK
    allowed_b, _ = ledger.check_quota("tenant-b", Usage(usd=10.0))
    assert allowed_b


def test_usage_persistence(ledger):
    """Usage records should persist."""
    usage = Usage(input_tokens=5000, usd=0.25)
    ledger.record_usage("acme-corp", usage, "T-1")

    # Read back
    period = ledger._get_period(daily=False)
    record = ledger.get_tenant_usage("acme-corp", period)

    assert record.usage.input_tokens == 5000
    assert record.usage.usd == 0.25


def test_daily_and_monthly_separate(ledger):
    """Daily and monthly usage should be tracked separately."""
    usage = Usage(usd=10.0)
    ledger.record_usage("acme-corp", usage, "T-1")

    # Check both aggregates updated
    monthly_period = ledger._get_period(daily=False)
    daily_period = ledger._get_period(daily=True)

    monthly = ledger.get_tenant_usage("acme-corp", monthly_period)
    daily = ledger.get_tenant_usage("acme-corp", daily_period)

    assert monthly.usage.usd == 10.0
    assert daily.usage.usd == 10.0
    assert monthly.period != daily.period
