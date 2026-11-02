"""Kill switch: global and per-tenant pause/resume for the factory.

The kill switch stops new work from being scheduled and signals in-flight tasks to
checkpoint and gracefully stop. This is critical for:
- Emergency stops during incidents
- Maintenance windows
- Per-tenant rate limiting or quota enforcement
- Graceful shutdown before infrastructure changes
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class KillSwitchState(StrEnum):
    """State of the kill switch."""
    RUNNING = "running"
    PAUSED = "paused"


class KillSwitchStatus(BaseModel):
    """Current status of a kill switch (global or per-tenant)."""
    
    state: KillSwitchState = KillSwitchState.RUNNING
    paused_at: str | None = None
    paused_by: str | None = None
    reason: str | None = None
    tenant_id: str | None = Field(default=None, description="None for global")
    
    def is_paused(self) -> bool:
        return self.state == KillSwitchState.PAUSED


class KillSwitchStore(Protocol):
    """Storage backend for kill switch state."""
    
    def get_global(self) -> KillSwitchStatus: ...
    def set_global(self, status: KillSwitchStatus) -> None: ...
    def get_tenant(self, tenant_id: str) -> KillSwitchStatus: ...
    def set_tenant(self, tenant_id: str, status: KillSwitchStatus) -> None: ...
    def list_tenants(self) -> dict[str, KillSwitchStatus]: ...


class FileKillSwitchStore:
    """File-based kill switch state store for local/v1 deployments."""
    
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._global = self.root / "global.json"
        self._tenants = self.root / "tenants"
        self._tenants.mkdir(exist_ok=True)
    
    def get_global(self) -> KillSwitchStatus:
        if not self._global.exists():
            return KillSwitchStatus()
        return KillSwitchStatus.model_validate_json(self._global.read_text())
    
    def set_global(self, status: KillSwitchStatus) -> None:
        tmp = self._global.with_suffix(".tmp")
        tmp.write_text(status.model_dump_json(indent=2))
        os.replace(tmp, self._global)
    
    def get_tenant(self, tenant_id: str) -> KillSwitchStatus:
        path = self._tenants / f"{tenant_id}.json"
        if not path.exists():
            return KillSwitchStatus(tenant_id=tenant_id)
        return KillSwitchStatus.model_validate_json(path.read_text())
    
    def set_tenant(self, tenant_id: str, status: KillSwitchStatus) -> None:
        path = self._tenants / f"{tenant_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(status.model_dump_json(indent=2))
        os.replace(tmp, path)
    
    def list_tenants(self) -> dict[str, KillSwitchStatus]:
        tenants = {}
        for path in self._tenants.glob("*.json"):
            if path.stem != "global":
                status = KillSwitchStatus.model_validate_json(path.read_text())
                tenants[path.stem] = status
        return tenants


class KillSwitch:
    """Kill switch controller for the factory.
    
    Checks:
    - Global pause blocks everything
    - Per-tenant pause blocks only that tenant's work
    
    The orchestrator should check is_paused() before scheduling new work.
    In-flight tasks should check periodically and checkpoint+stop if paused.
    """
    
    def __init__(self, store: KillSwitchStore):
        self.store = store
    
    def is_paused(self, tenant_id: str | None = None) -> bool:
        """Check if work should be paused.
        
        Args:
            tenant_id: Tenant to check, or None for default tenant
            
        Returns:
            True if either global or tenant-specific pause is active
        """
        if self.store.get_global().is_paused():
            return True
        
        if tenant_id:
            return self.store.get_tenant(tenant_id).is_paused()
        
        return False
    
    def pause(
        self,
        *,
        tenant_id: str | None = None,
        reason: str,
        paused_by: str = "operator"
    ) -> KillSwitchStatus:
        """Pause the factory (globally or per-tenant).
        
        Args:
            tenant_id: Tenant to pause, or None for global pause
            reason: Human-readable reason for the pause
            paused_by: Operator or system that initiated the pause
            
        Returns:
            Updated status
        """
        status = KillSwitchStatus(
            state=KillSwitchState.PAUSED,
            paused_at=datetime.now(UTC).isoformat(),
            paused_by=paused_by,
            reason=reason,
            tenant_id=tenant_id,
        )
        
        if tenant_id is None:
            self.store.set_global(status)
        else:
            self.store.set_tenant(tenant_id, status)
        
        return status
    
    def resume(self, *, tenant_id: str | None = None) -> KillSwitchStatus:
        """Resume the factory (globally or per-tenant).
        
        Args:
            tenant_id: Tenant to resume, or None for global resume
            
        Returns:
            Updated status
        """
        status = KillSwitchStatus(tenant_id=tenant_id)
        
        if tenant_id is None:
            self.store.set_global(status)
        else:
            self.store.set_tenant(tenant_id, status)
        
        return status
    
    def status(self, *, tenant_id: str | None = None) -> KillSwitchStatus:
        """Get current kill switch status.
        
        Args:
            tenant_id: Tenant to query, or None for global status
            
        Returns:
            Current status
        """
        if tenant_id is None:
            return self.store.get_global()
        return self.store.get_tenant(tenant_id)
    
    def list_all(self) -> dict[str, KillSwitchStatus]:
        """List all kill switch states (global + all tenants).
        
        Returns:
            Map of "global" and tenant IDs to their status
        """
        result = {"global": self.store.get_global()}
        result.update(self.store.list_tenants())
        return result
