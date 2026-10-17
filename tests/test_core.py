from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from router.audit import AuditStore
from router.core import TaskManager, TaskRejected
from router.models import TaskRequest
from router.provider import MockProvider


async def make_manager(
    tmp_path: Path,
    *,
    provider: MockProvider | None = None,
    config_text: str | None = None,
) -> TaskManager:
    config = tmp_path / "routing.yaml"
    config.write_text(config_text or Path("config/routing.yaml").read_text())
    return TaskManager(
        str(config), audit=AuditStore(":memory:"), provider=provider or MockProvider(delay=0.01)
    )


@pytest.mark.asyncio
async def test_bulkhead_rejects_when_pool_is_full(tmp_path: Path) -> None:
    config_text = """
pools:
  summarize: {concurrency: 1, queue_size: 1, token_budget: 1000, model: mock-primary,
    fallback_model: mock-fallback}
tenants:
  default: {concurrency: 1, queue_size: 1, token_budget: 1000}
skills: {summarize: summarize}
"""
    manager = await make_manager(
        tmp_path, config_text=config_text, provider=MockProvider(delay=0.05)
    )
    first = await manager.submit(
        TaskRequest(tenant="tenant-a", skill="summarize", payload={"text": "a"})
    )
    with pytest.raises(TaskRejected, match="tenant bulkhead full"):
        await manager.submit(
            TaskRequest(tenant="tenant-a", skill="summarize", payload={"text": "b"})
        )
    await manager.get(first.id, wait=True)
    assert any(event["event_type"] == "rejection" for event in manager.audit_events())


@pytest.mark.asyncio
async def test_tenant_isolation_allows_other_tenant(tmp_path: Path) -> None:
    config_text = """
pools:
  summarize: {concurrency: 2, queue_size: 4, token_budget: 1000, model: mock-primary,
    fallback_model: mock-fallback}
tenants:
  default: {concurrency: 1, queue_size: 1, token_budget: 1000}
skills: {summarize: summarize}
"""
    manager = await make_manager(
        tmp_path, config_text=config_text, provider=MockProvider(delay=0.02)
    )
    first = await manager.submit(
        TaskRequest(tenant="tenant-a", skill="summarize", payload={"text": "a"})
    )
    second = await manager.submit(
        TaskRequest(tenant="tenant-b", skill="summarize", payload={"text": "b"})
    )
    await manager.wait_for_idle()
    assert (await manager.get(first.id)).status == "succeeded"
    assert (await manager.get(second.id)).status == "succeeded"


@pytest.mark.asyncio
async def test_breaker_trips_half_opens_recovers_and_uses_fallback(tmp_path: Path) -> None:
    provider = MockProvider(delay=0, fail_tenants={"tenant-a"})
    manager = await make_manager(tmp_path, provider=provider)
    manager.breaker_failure_threshold = 2
    manager.breakers["mock-primary"].failure_threshold = 2
    manager.breakers["mock-primary"].cooldown = 0.02

    for _ in range(2):
        response = await manager.submit(
            TaskRequest(tenant="tenant-a", skill="summarize", payload={"text": "bad"})
        )
        await manager.get(response.id, wait=True)
    assert manager.breakers["mock-primary"].state == "open"
    transitions = [e for e in manager.audit_events() if e["event_type"] == "breaker_transition"]
    assert any(e["reason"] == "closed->open" for e in transitions)

    await asyncio.sleep(0.03)
    response = await manager.submit(
        TaskRequest(tenant="tenant-a", skill="summarize", payload={"text": "still bad"})
    )
    result = await manager.get(response.id, wait=True)
    assert result.status == "succeeded"
    assert result.fallback is True
    assert manager.breakers["mock-primary"].state == "open"

    provider.fail_tenants.clear()
    await asyncio.sleep(0.03)
    response = await manager.submit(
        TaskRequest(tenant="tenant-b", skill="summarize", payload={"text": "recovered"})
    )
    result = await manager.get(response.id, wait=True)
    assert result.status == "succeeded"
    assert result.fallback is False
    assert manager.breakers["mock-primary"].state == "closed"


@pytest.mark.asyncio
async def test_audit_is_append_only(tmp_path: Path) -> None:
    store = AuditStore(str(tmp_path / "audit.db"))
    row_id = store.append("routing_decision", tenant="tenant-a")
    with pytest.raises(Exception, match="append-only"):
        store._conn.execute("UPDATE audit_events SET tenant='changed' WHERE id=?", (row_id,))
    with pytest.raises(Exception, match="append-only"):
        store._conn.execute("DELETE FROM audit_events WHERE id=?", (row_id,))
    assert store.list()[0]["tenant"] == "tenant-a"
