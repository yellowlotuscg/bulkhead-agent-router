#!/usr/bin/env python3
"""Run a small deterministic baseline/chaos latency comparison."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from router.audit import AuditStore
from router.core import TaskManager
from router.models import TaskRequest
from router.provider import MockProvider


async def measure(manager: TaskManager, tenant: str, count: int = 20) -> tuple[list[float], int]:
    latencies: list[float] = []
    failures = 0
    for index in range(count):
        started = time.perf_counter()
        response = await manager.submit(
            TaskRequest(tenant=tenant, skill="summarize", payload={"text": f"ticket {index}"})
        )
        result = await manager.get(response.id, wait=True)
        latencies.append((time.perf_counter() - started) * 1000)
        failures += int(result is None or result.status == "failed")
    return latencies, failures


def p95(values: list[float]) -> float:
    values = sorted(values)
    return values[max(0, int(len(values) * 0.95) - 1)]


async def main() -> None:
    config = "config/routing.yaml"
    cases = []
    for label, fail_tenants in (("baseline", set()), ("chaos", {"tenant-acme"})):
        manager = TaskManager(
            config, audit=AuditStore(":memory:"), provider=MockProvider(fail_tenants=fail_tenants)
        )
        a_lat, a_fail = await measure(manager, "tenant-acme")
        b_lat, b_fail = await measure(manager, "tenant-globex")
        cases.extend(
            [
                (label, "tenant-acme", p95(a_lat), a_fail),
                (label, "tenant-globex", p95(b_lat), b_fail),
            ]
        )
        manager.close()
    print("scenario  tenant          p95_ms  failures")
    print("--------  --------------  ------  --------")
    for label, tenant, latency, failures in cases:
        print(f"{label:<8}  {tenant:<14}  {latency:6.2f}  {failures:8d}")


if __name__ == "__main__":
    asyncio.run(main())
