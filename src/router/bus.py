from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BusMessage:
    tenant: str
    body: dict[str, Any]


class InMemoryBus:
    """Session-like in-memory bus: each tenant has an independent FIFO queue."""

    def __init__(self, maxsize: int = 100) -> None:
        self._queues: dict[str, asyncio.Queue[BusMessage]] = defaultdict(
            lambda: asyncio.Queue(maxsize=maxsize)
        )

    async def send(self, tenant: str, body: dict[str, Any]) -> None:
        await self._queues[tenant].put(BusMessage(tenant, body))

    async def receive(self, tenant: str) -> BusMessage:
        return await self._queues[tenant].get()


class AzureServiceBusBus:
    """Optional session-enabled Service Bus adapter keyed by tenant."""

    def __init__(self, fully_qualified_namespace: str, queue_name: str) -> None:
        try:
            from azure.identity.aio import DefaultAzureCredential
            from azure.servicebus.aio import ServiceBusClient
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "install the azure optional dependencies to use Service Bus"
            ) from exc
        self._credential = DefaultAzureCredential()
        self._client = ServiceBusClient(fully_qualified_namespace, self._credential)
        self._queue_name = queue_name

    async def send(self, tenant: str, body: dict[str, Any]) -> None:
        async with self._client:
            sender = self._client.get_queue_sender(self._queue_name)
            async with sender:
                from azure.servicebus import ServiceBusMessage

                await sender.send_messages(ServiceBusMessage(str(body), session_id=tenant))

    async def receive(self, tenant: str) -> Any:
        receiver = self._client.get_queue_receiver(self._queue_name, session_id=tenant)
        async with receiver:
            return await receiver.receive_messages(max_wait_time=5, max_message_count=1)


class CosmosAuditAdapter:
    """Optional Cosmos DB sink with the same append operation as AuditStore."""

    def __init__(self, endpoint: str, database: str, container: str, credential: Any) -> None:
        try:
            from azure.cosmos import CosmosClient
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("install the azure optional dependencies to use Cosmos DB") from exc
        self._container = (
            CosmosClient(endpoint, credential)
            .get_database_client(database)
            .get_container_client(container)
        )

    def append(self, event: dict[str, Any]) -> None:
        self._container.create_item({"id": event.get("id", str(id(event))), **event})
