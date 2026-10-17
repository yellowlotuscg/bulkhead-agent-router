from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .audit import AuditStore
from .breaker import CircuitBreaker, CircuitOpenError
from .config import PoolConfig, RouterConfig, TenantConfig
from .models import TaskRequest, TaskResponse
from .provider import AzureOpenAIProvider, MockProvider, ModelError, ModelProvider


@dataclass
class TaskRecord:
    id: str
    request: TaskRequest
    status: str
    created_at: datetime
    result: Optional[Any] = None
    error: Optional[str] = None
    fallback: bool = False
    completed_at: Optional[datetime] = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def response(self) -> TaskResponse:
        return TaskResponse(
            id=self.id,
            tenant=self.request.tenant,
            skill=self.request.skill,
            status=self.status,
            result=self.result,
            error=self.error,
            fallback=self.fallback,
            created_at=self.created_at,
            completed_at=self.completed_at,
        )


@dataclass
class Compartment:
    concurrency: int
    queue_size: int
    token_budget: int
    occupancy: int = 0
    tokens_used: int = 0
    semaphore: Optional[asyncio.Semaphore] = None
    queue: Optional[asyncio.Queue] = None

    def initialize(self) -> None:
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.queue = asyncio.Queue(maxsize=self.queue_size)


class TaskRejected(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TaskManager:
    """Skill router with independent tenant and pool compartments."""

    def __init__(
        self,
        config_path: str = "config/routing.yaml",
        *,
        audit: Optional[AuditStore] = None,
        provider: Optional[ModelProvider] = None,
        breaker_failure_threshold: int = 3,
        breaker_cooldown: float = 0.2,
    ) -> None:
        self.config = RouterConfig.from_file(config_path)
        self.audit = audit or AuditStore(os.getenv("AUDIT_DB", "data/audit.db"))
        self.provider = provider or self._provider_from_env()
        self.pools = {
            name: self._pool_compartment(config) for name, config in self.config.pools.items()
        }
        self.tenants: dict[str, Compartment] = {}
        self.breakers: dict[str, CircuitBreaker] = {}
        self._tasks: dict[str, TaskRecord] = {}
        self._admission_lock = asyncio.Lock()
        self._background: set[asyncio.Task[None]] = set()
        self.breaker_failure_threshold = breaker_failure_threshold
        self.breaker_cooldown = breaker_cooldown

        for pool in self.config.pools.values():
            self._get_breaker(pool.model)
            self._get_breaker(pool.fallback_model)

    @staticmethod
    def _pool_compartment(config: PoolConfig) -> Compartment:
        compartment = Compartment(config.concurrency, config.queue_size, config.token_budget)
        compartment.initialize()
        return compartment

    def _provider_from_env(self) -> ModelProvider:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if endpoint and api_key and deployment:
            return AzureOpenAIProvider(
                endpoint,
                api_key,
                deployment,
                os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        return MockProvider()

    def _get_breaker(self, endpoint: str) -> CircuitBreaker:
        if endpoint not in self.breakers:

            async def on_transition(old: str, new: str, endpoint: str = endpoint) -> None:
                self.audit.append(
                    "breaker_transition",
                    reason=f"{old}->{new}",
                    metadata={"endpoint": endpoint, "from": old, "to": new},
                )

            self.breakers[endpoint] = CircuitBreaker(
                endpoint,
                self.breaker_failure_threshold,
                self.breaker_cooldown,
                on_transition,
            )
        return self.breakers[endpoint]

    def _tenant_compartment(self, tenant: str) -> Compartment:
        if tenant not in self.tenants:
            config: TenantConfig = self.config.tenant(tenant)
            compartment = Compartment(config.concurrency, config.queue_size, config.token_budget)
            compartment.initialize()
            self.tenants[tenant] = compartment
        return self.tenants[tenant]

    @staticmethod
    def _estimate_tokens(payload: dict[str, Any]) -> int:
        return max(1, len(json.dumps(payload, sort_keys=True, separators=(",", ":"))) // 4)

    async def submit(self, request: TaskRequest) -> TaskResponse:
        task_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc)
        record = TaskRecord(task_id, request, "pending", created_at)
        pool_name = self.config.skills.get(request.skill)
        if pool_name is None:
            raise TaskRejected(f"skill not routed: {request.skill}")
        pool_config = self.config.pools[pool_name]
        tenant_compartment = self._tenant_compartment(request.tenant)
        pool_compartment = self.pools[pool_name]
        tokens = self._estimate_tokens(request.payload)

        async with self._admission_lock:
            reason = None
            if tenant_compartment.occupancy >= tenant_compartment.queue_size:
                reason = f"tenant bulkhead full: {request.tenant}"
            elif pool_compartment.occupancy >= pool_compartment.queue_size:
                reason = f"pool bulkhead full: {pool_name}"
            elif tenant_compartment.tokens_used + tokens > tenant_compartment.token_budget:
                reason = f"tenant token budget exceeded: {request.tenant}"
            elif pool_compartment.tokens_used + tokens > pool_compartment.token_budget:
                reason = f"pool token budget exceeded: {pool_name}"
            if reason:
                self.audit.append(
                    "rejection",
                    task_id=task_id,
                    tenant=request.tenant,
                    skill=request.skill,
                    pool=pool_name,
                    reason=reason,
                    metadata={"tokens": tokens},
                )
                raise TaskRejected(reason)
            assert tenant_compartment.queue is not None and pool_compartment.queue is not None
            tenant_compartment.queue.put_nowait(None)
            pool_compartment.queue.put_nowait(None)
            tenant_compartment.occupancy += 1
            tenant_compartment.tokens_used += tokens
            pool_compartment.occupancy += 1
            pool_compartment.tokens_used += tokens
            self._tasks[task_id] = record
            self.audit.append(
                "routing_decision",
                task_id=task_id,
                tenant=request.tenant,
                skill=request.skill,
                pool=pool_name,
                metadata={"model": pool_config.model, "tokens": tokens},
            )

        worker = asyncio.create_task(self._execute(record, pool_name, tokens))
        self._background.add(worker)
        worker.add_done_callback(self._background.discard)
        return record.response()

    async def _execute(self, record: TaskRecord, pool_name: str, tokens: int) -> None:
        pool = self.pools[pool_name]
        tenant = self._tenant_compartment(record.request.tenant)
        pool_config = self.config.pools[pool_name]
        assert pool.semaphore is not None and tenant.semaphore is not None
        try:
            async with pool.semaphore, tenant.semaphore:
                record.status = "running"
                try:
                    result = await self._call_with_fallback(record, pool_config)
                    record.result, record.fallback = result
                    record.status = "succeeded"
                except Exception as exc:  # task state should always become observable
                    record.status = "failed"
                    record.error = str(exc)
                    self.audit.append(
                        "task_failure",
                        task_id=record.id,
                        tenant=record.request.tenant,
                        skill=record.request.skill,
                        pool=pool_name,
                        reason=str(exc),
                    )
        finally:
            record.completed_at = datetime.now(timezone.utc)
            record.done.set()
            async with self._admission_lock:
                tenant.occupancy = max(0, tenant.occupancy - 1)
                pool.occupancy = max(0, pool.occupancy - 1)
                if tenant.queue is not None:
                    tenant.queue.get_nowait()
                if pool.queue is not None:
                    pool.queue.get_nowait()

    async def _call_with_fallback(
        self, record: TaskRecord, pool_config: PoolConfig
    ) -> tuple[Any, bool]:
        primary = self._get_breaker(pool_config.model)
        try:
            await primary.allow()
            result = await self.provider.generate(
                model=pool_config.model,
                skill=record.request.skill,
                payload=record.request.payload,
                tenant=record.request.tenant,
            )
            await primary.success()
            return result, False
        except (CircuitOpenError, ModelError) as primary_error:
            if isinstance(primary_error, ModelError):
                await primary.failure()
            fallback = self._get_breaker(pool_config.fallback_model)
            try:
                await fallback.allow()
                result = await self.provider.generate(
                    model=pool_config.fallback_model,
                    skill=record.request.skill,
                    payload=record.request.payload,
                    tenant=record.request.tenant,
                )
                await fallback.success()
                self.audit.append(
                    "fallback_used",
                    task_id=record.id,
                    tenant=record.request.tenant,
                    skill=record.request.skill,
                    pool=pool_config.name,
                    reason=str(primary_error),
                    metadata={"model": pool_config.fallback_model},
                )
                return result, True
            except (CircuitOpenError, ModelError) as fallback_error:
                if isinstance(fallback_error, ModelError):
                    await fallback.failure()
                message = f"primary and fallback failed: {primary_error}; {fallback_error}"
                raise ModelError(message) from fallback_error

    async def get(self, task_id: str, wait: bool = False) -> Optional[TaskResponse]:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        if wait:
            await record.done.wait()
        return record.response()

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.audit.list(limit)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "breakers": {
                name: {"state": breaker.state, "failures": breaker.failures}
                for name, breaker in self.breakers.items()
            },
            "pools": {
                name: {"occupancy": compartment.occupancy, "queue_size": compartment.queue_size}
                for name, compartment in self.pools.items()
            },
        }

    async def wait_for_idle(self) -> None:
        if self._background:
            await asyncio.gather(*tuple(self._background))

    def close(self) -> None:
        self.audit.close()
