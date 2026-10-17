from __future__ import annotations

import os

try:
    import azure.functions as func
except ImportError:  # Allows local imports and tests without Azure Functions installed.
    func = None

from router.core import TaskManager
from router.models import TaskRequest

_manager = TaskManager(
    config_path=os.getenv("ROUTING_CONFIG", "config/routing.yaml"),
)

if func is not None:
    function_app = func.FunctionApp()

    @function_app.function_name(name="route_service_bus_task")
    @function_app.service_bus_queue_trigger(
        arg_name="message",
        queue_name="tasks",
        connection="SERVICE_BUS_CONNECTION",
        is_sessions_enabled=True,
    )
    async def route_service_bus_task(message: func.ServiceBusMessage) -> None:
        body = message.get_body().decode("utf-8")
        await _manager.submit(TaskRequest.model_validate_json(body))
else:
    function_app = None
