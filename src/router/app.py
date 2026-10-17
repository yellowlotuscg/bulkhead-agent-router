from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .core import TaskManager, TaskRejected
from .models import TaskRequest


def create_app(manager: Optional[TaskManager] = None) -> FastAPI:
    router_manager = manager or TaskManager(
        config_path=os.getenv("ROUTING_CONFIG", "config/routing.yaml"),
    )
    app = FastAPI(title="bulkhead-agent-router", version="0.1.0")
    app.state.manager = router_manager

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return router_manager.health()

    @app.post("/tasks", status_code=202)
    async def create_task(request: TaskRequest) -> dict[str, Any]:
        try:
            return (await router_manager.submit(request)).model_dump(mode="json")
        except TaskRejected as exc:
            raise HTTPException(status_code=429, detail={"reason": exc.reason}) from exc

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str, wait: bool = Query(False)) -> dict[str, Any]:
        result = await router_manager.get(task_id, wait=wait)
        if result is None:
            raise HTTPException(status_code=404, detail="task not found")
        return result.model_dump(mode="json")

    @app.get("/audit")
    async def audit(limit: int = Query(100, ge=1, le=1000), html: bool = False) -> Any:
        events = router_manager.audit_events(limit)
        if not html:
            return {"events": events}
        rows = "".join(
            f"<tr><td>{event['id']}</td><td>{event['timestamp']}</td><td>{event['event_type']}</td>"
            f"<td>{event.get('tenant') or ''}</td><td>{event.get('reason') or ''}</td></tr>"
            for event in events
        )
        return HTMLResponse(
            "<html><body><h1>Audit</h1><table border='1'><tr><th>id</th><th>timestamp</th>"
            f"<th>event</th><th>tenant</th><th>reason</th></tr>{rows}</table></body></html>"
        )

    return app


app = create_app()
