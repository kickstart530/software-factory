"""Multi-tenant budget ledger: track spend per tenant, enforce quotas.

The ledger records all model/tool costs and enforces per-tenant budget limits.
This ensures tenants cannot exceed their allocated budgets and provides
audit trails for cost allocation.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from factory.schemas import Usage


class TenantBudgetConfig(BaseModel):
    """Budget configuration for a tenant."""
    
    tenant_id: str
    monthly_usd_limit: float | None = None
    daily_usd_limit: float | None = None
    tokens_per_task_limit: int | None = None
    enabled: bool = True


class TenantUsageRecord(BaseModel):
    """Usage record for a tenant in a time period."""
    
    tenant_id: str
    period: str = Field(description="e.g., '2024-03' for monthly, '2024-03-15' for daily")
    usage: Usage = Field(default_factory=Usage)
    last_updated: float = Field(default_factory=time.time)


class BudgetLedger(Protocol):
    """Storage and enforcement for multi-tenant budgets."""
    
    def record_usage(self, tenant_id: str, usage: Usage, task_id: str) -> None: ...
    def get_tenant_usage(self, tenant_id: str, period: str) -> TenantUsageRecord: ...
    def get_tenant_config(self, tenant_id: str) -> TenantBudgetConfig: ...
    def set_tenant_config(self, config: TenantBudgetConfig) -> None: ...
    def check_quota(self, tenant_id: str, usage: Usage) -> tuple[bool, str | None]: ...


class FileBudgetLedger:
    """File-based budget ledger for local/v1 deployments."""
    
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._configs = self.root / "configs"
        self._usage = self.root / "usage"
        self._configs.mkdir(exist_ok=True)
        self._usage.mkdir(exist_ok=True)
    
    def _get_period(self, daily: bool = False) -> str:
        """Get current period string."""
        t = time.gmtime()
        if daily:
            return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        return f"{t.tm_year:04d}-{t.tm_mon:02d}"
    
    def record_usage(self, tenant_id: str, usage: Usage, task_id: str) -> None:
        """Record usage for a tenant."""
        # Update monthly usage
        monthly = self._get_period(daily=False)
        monthly_rec = self.get_tenant_usage(tenant_id, monthly)
        monthly_rec.usage.input_tokens += usage.input_tokens
        monthly_rec.usage.output_tokens += usage.output_tokens
        monthly_rec.usage.cache_read_tokens += usage.cache_read_tokens
        monthly_rec.usage.cache_write_tokens += usage.cache_write_tokens
        monthly_rec.usage.usd += usage.usd
        monthly_rec.usage.turns += usage.turns
        monthly_rec.usage.tool_calls += usage.tool_calls
        monthly_rec.usage.wall_clock_s += usage.wall_clock_s
        monthly_rec.last_updated = time.time()
        self._save_usage(monthly_rec)
        
        # Update daily usage
        daily = self._get_period(daily=True)
        daily_rec = self.get_tenant_usage(tenant_id, daily)
        daily_rec.usage.input_tokens += usage.input_tokens
        daily_rec.usage.output_tokens += usage.output_tokens
        daily_rec.usage.cache_read_tokens += usage.cache_read_tokens
        daily_rec.usage.cache_write_tokens += usage.cache_write_tokens
        daily_rec.usage.usd += usage.usd
        daily_rec.usage.turns += usage.turns
        daily_rec.usage.tool_calls += usage.tool_calls
        daily_rec.usage.wall_clock_s += usage.wall_clock_s
        daily_rec.last_updated = time.time()
        self._save_usage(daily_rec)
    
    def get_tenant_usage(self, tenant_id: str, period: str) -> TenantUsageRecord:
        """Get usage for a tenant in a period."""
        path = self._usage / tenant_id / f"{period}.json"
        if not path.exists():
            return TenantUsageRecord(tenant_id=tenant_id, period=period)
        return TenantUsageRecord.model_validate_json(path.read_text())
    
    def _save_usage(self, record: TenantUsageRecord) -> None:
        """Save usage record."""
        tenant_dir = self._usage / record.tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        path = tenant_dir / f"{record.period}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(record.model_dump_json(indent=2))
        os.replace(tmp, path)
    
    def get_tenant_config(self, tenant_id: str) -> TenantBudgetConfig:
        """Get budget config for a tenant."""
        path = self._configs / f"{tenant_id}.json"
        if not path.exists():
            return TenantBudgetConfig(tenant_id=tenant_id)
        return TenantBudgetConfig.model_validate_json(path.read_text())
    
    def set_tenant_config(self, config: TenantBudgetConfig) -> None:
        """Set budget config for a tenant."""
        path = self._configs / f"{config.tenant_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(config.model_dump_json(indent=2))
        os.replace(tmp, path)
    
    def check_quota(self, tenant_id: str, usage: Usage) -> tuple[bool, str | None]:
        """Check if additional usage would exceed tenant quota.
        
        Args:
            tenant_id: Tenant to check
            usage: Proposed additional usage
            
        Returns:
            (allowed, reason) where reason is set if not allowed
        """
        config = self.get_tenant_config(tenant_id)
        
        if not config.enabled:
            return False, f"Tenant {tenant_id} is disabled"
        
        # Check monthly limit
        if config.monthly_usd_limit is not None:
            monthly = self._get_period(daily=False)
            monthly_usage = self.get_tenant_usage(tenant_id, monthly)
            if monthly_usage.usage.usd + usage.usd > config.monthly_usd_limit:
                return False, (
                    f"Would exceed monthly budget: "
                    f"${monthly_usage.usage.usd:.2f} + ${usage.usd:.2f} "
                    f"> ${config.monthly_usd_limit:.2f}"
                )
        
        # Check daily limit
        if config.daily_usd_limit is not None:
            daily = self._get_period(daily=True)
            daily_usage = self.get_tenant_usage(tenant_id, daily)
            if daily_usage.usage.usd + usage.usd > config.daily_usd_limit:
                return False, (
                    f"Would exceed daily budget: "
                    f"${daily_usage.usage.usd:.2f} + ${usage.usd:.2f} "
                    f"> ${config.daily_usd_limit:.2f}"
                )
        
        # Check per-task token limit
        if config.tokens_per_task_limit is not None:
            if usage.total_tokens > config.tokens_per_task_limit:
                return False, (
                    f"Task would use {usage.total_tokens} tokens, "
                    f"limit is {config.tokens_per_task_limit}"
                )
        
        return True, None
    
    def list_all_usage(self, period: str) -> dict[str, TenantUsageRecord]:
        """List usage for all tenants in a period."""
        result = {}
        if not self._usage.exists():
            return result
        
        for tenant_dir in self._usage.iterdir():
            if tenant_dir.is_dir():
                tenant_id = tenant_dir.name
                path = tenant_dir / f"{period}.json"
                if path.exists():
                    result[tenant_id] = TenantUsageRecord.model_validate_json(path.read_text())
        
        return result
