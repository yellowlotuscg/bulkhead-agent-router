from __future__ import annotations

from fastapi.testclient import TestClient

from router.app import create_app
from router.audit import AuditStore
from router.core import TaskManager
from router.provider import MockProvider


def test_http_endpoints(tmp_path) -> None:
    manager = TaskManager(
        "config/routing.yaml", audit=AuditStore(":memory:"), provider=MockProvider(delay=0)
    )
    client = TestClient(create_app(manager))
    response = client.post(
        "/tasks",
        json={"tenant": "tenant-acme", "skill": "classify", "payload": {"text": "invoice charge"}},
    )
    assert response.status_code == 202
    task_id = response.json()["id"]
    result = client.get(f"/tasks/{task_id}?wait=true")
    assert result.status_code == 200
    assert result.json()["status"] == "succeeded"
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/audit").json()["events"]
