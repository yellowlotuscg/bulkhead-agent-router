from __future__ import annotations

import time
from pathlib import Path

import pytest

from router.audit import AuditStore
from router.core import TaskManager
from router.models import TaskRequest
from router.provider import MockProvider


async def run_tenant(manager: TaskManager, tenant: str, count: int = 18) -> tuple[list[float], int]:
    latencies = []
    failures = 0
    for index in range(count):
        started = time.perf_counter()
        accepted = await manager.submit(
            TaskRequest(tenant=tenant, skill="summarize", payload={"text": str(index)})
        )
        result = await manager.get(accepted.id, wait=True)
        latencies.append((time.perf_counter() - started) * 1000)
        failures += int(result.status == "failed")
    return latencies, failures


@pytest.mark.asyncio
async def test_chaos_tenant_does_not_fail_healthy_tenant(tmp_path: Path) -> None:
    provider = MockProvider(delay=0.001, fail_tenants={"tenant-a"})
    manager = TaskManager("config/routing.yaml", audit=AuditStore(":memory:"), provider=provider)
    baseline_provider = MockProvider(delay=0.001)
    baseline = TaskManager(
        "config/routing.yaml", audit=AuditStore(":memory:"), provider=baseline_provider
    )
    baseline_latencies, _ = await run_tenant(baseline, "tenant-b")
    await run_tenant(manager, "tenant-a")
    healthy_latencies, healthy_failures = await run_tenant(manager, "tenant-b")
    baseline_p95 = sorted(baseline_latencies)[int(len(baseline_latencies) * 0.95) - 1]
    healthy_p95 = sorted(healthy_latencies)[int(len(healthy_latencies) * 0.95) - 1]
    assert healthy_failures == 0
    assert healthy_p95 <= baseline_p95 * 3 + 5
