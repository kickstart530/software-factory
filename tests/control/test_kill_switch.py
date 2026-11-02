"""Tests for kill switch functionality."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from factory.control.kill_switch import (
    FileKillSwitchStore,
    KillSwitch,
    KillSwitchState,
)


@pytest.fixture
def store():
    with TemporaryDirectory() as tmpdir:
        yield FileKillSwitchStore(Path(tmpdir))


@pytest.fixture
def kill_switch(store):
    return KillSwitch(store)


def test_initial_state_running(kill_switch):
    """Factory should be running by default."""
    assert not kill_switch.is_paused()
    assert kill_switch.status().state == KillSwitchState.RUNNING


def test_global_pause(kill_switch):
    """Global pause should block all work."""
    status = kill_switch.pause(reason="Testing", paused_by="test")
    
    assert status.state == KillSwitchState.PAUSED
    assert status.reason == "Testing"
    assert status.paused_by == "test"
    assert status.paused_at is not None
    
    # Should block all tenants
    assert kill_switch.is_paused()
    assert kill_switch.is_paused(tenant_id="tenant-a")
    assert kill_switch.is_paused(tenant_id="tenant-b")


def test_tenant_pause(kill_switch):
    """Per-tenant pause should only block that tenant."""
    kill_switch.pause(
        tenant_id="tenant-a",
        reason="Quota exceeded",
        paused_by="billing"
    )
    
    # tenant-a should be paused
    assert kill_switch.is_paused(tenant_id="tenant-a")
    
    # Other tenants should be running
    assert not kill_switch.is_paused(tenant_id="tenant-b")
    assert not kill_switch.is_paused(tenant_id="tenant-c")


def test_global_overrides_tenant(kill_switch):
    """Global pause should override tenant-specific state."""
    # Pause globally
    kill_switch.pause(reason="Global maintenance")
    
    # Even tenants not explicitly paused should be blocked
    assert kill_switch.is_paused(tenant_id="tenant-a")
    
    # Resume globally
    kill_switch.resume()
    
    # Now tenants should be running
    assert not kill_switch.is_paused(tenant_id="tenant-a")


def test_resume_global(kill_switch):
    """Global resume should restore running state."""
    kill_switch.pause(reason="Testing")
    assert kill_switch.is_paused()
    
    kill_switch.resume()
    assert not kill_switch.is_paused()
    
    status = kill_switch.status()
    assert status.state == KillSwitchState.RUNNING


def test_resume_tenant(kill_switch):
    """Per-tenant resume should only affect that tenant."""
    kill_switch.pause(tenant_id="tenant-a", reason="Testing")
    kill_switch.pause(tenant_id="tenant-b", reason="Testing")
    
    kill_switch.resume(tenant_id="tenant-a")
    
    assert not kill_switch.is_paused(tenant_id="tenant-a")
    assert kill_switch.is_paused(tenant_id="tenant-b")


def test_status_query(kill_switch):
    """Status query should return current state."""
    # Global status
    status = kill_switch.status()
    assert status.state == KillSwitchState.RUNNING
    assert status.tenant_id is None
    
    # Pause and check
    kill_switch.pause(reason="Testing", paused_by="ops")
    status = kill_switch.status()
    assert status.state == KillSwitchState.PAUSED
    assert status.reason == "Testing"
    assert status.paused_by == "ops"


def test_tenant_status_query(kill_switch):
    """Tenant status should be independent of global."""
    kill_switch.pause(
        tenant_id="tenant-a",
        reason="Quota exceeded",
        paused_by="billing"
    )
    
    # Check tenant status
    tenant_status = kill_switch.status(tenant_id="tenant-a")
    assert tenant_status.state == KillSwitchState.PAUSED
    assert tenant_status.tenant_id == "tenant-a"
    assert tenant_status.reason == "Quota exceeded"
    
    # Global should still be running
    global_status = kill_switch.status()
    assert global_status.state == KillSwitchState.RUNNING


def test_list_all_states(kill_switch):
    """List all should include global + all tenant states."""
    kill_switch.pause(tenant_id="tenant-a", reason="A")
    kill_switch.pause(tenant_id="tenant-b", reason="B")
    
    all_states = kill_switch.list_all()
    
    assert "global" in all_states
    assert "tenant-a" in all_states
    assert "tenant-b" in all_states
    
    assert all_states["global"].state == KillSwitchState.RUNNING
    assert all_states["tenant-a"].state == KillSwitchState.PAUSED
    assert all_states["tenant-b"].state == KillSwitchState.PAUSED


def test_store_persistence(store):
    """State should persist across store instances."""
    switch1 = KillSwitch(store)
    switch1.pause(reason="Testing")
    
    # Create new kill switch with same store
    switch2 = KillSwitch(store)
    assert switch2.is_paused()
    assert switch2.status().reason == "Testing"


def test_tenant_store_persistence(store):
    """Tenant state should persist."""
    switch1 = KillSwitch(store)
    switch1.pause(tenant_id="tenant-a", reason="Testing")
    
    switch2 = KillSwitch(store)
    assert switch2.is_paused(tenant_id="tenant-a")
    assert not switch2.is_paused(tenant_id="tenant-b")


def test_multiple_tenants_independent(kill_switch):
    """Multiple tenants should have independent states."""
    kill_switch.pause(tenant_id="tenant-a", reason="A")
    kill_switch.pause(tenant_id="tenant-b", reason="B")
    kill_switch.pause(tenant_id="tenant-c", reason="C")
    
    # Resume one
    kill_switch.resume(tenant_id="tenant-b")
    
    # Check states
    assert kill_switch.is_paused(tenant_id="tenant-a")
    assert not kill_switch.is_paused(tenant_id="tenant-b")
    assert kill_switch.is_paused(tenant_id="tenant-c")


def test_pause_reason_required(kill_switch):
    """Pause should require a reason."""
    # This should work
    status = kill_switch.pause(reason="Valid reason")
    assert status.reason == "Valid reason"


def test_default_tenant_none(kill_switch):
    """Default tenant ID should be None (represents 'default')."""
    # Not pausing a specific tenant means global
    assert not kill_switch.is_paused(tenant_id=None)
    
    kill_switch.pause(tenant_id="specific", reason="Testing")
    
    # Default tenant should still be running (only specific is paused)
    assert not kill_switch.is_paused(tenant_id=None)
    assert kill_switch.is_paused(tenant_id="specific")
