from __future__ import annotations

from pathlib import Path

from router.config import RouterConfig


def test_routing_table_contains_functional_pools() -> None:
    config = RouterConfig.from_file(Path("config/routing.yaml"))
    assert config.skills == {"summarize": "summarize", "classify": "classify", "extract": "extract"}
    assert config.pool_for("summarize").model == "mock-primary"
    assert config.pool_for("classify").concurrency == 2
