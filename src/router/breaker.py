from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable, Optional, Union


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class BreakerSnapshot:
    endpoint: str
    state: str
    failures: int


class CircuitBreaker:
    def __init__(
        self,
        endpoint: str,
        failure_threshold: int = 3,
        cooldown: float = 0.2,
        on_transition: Optional[Callable[[str, str], Union[Awaitable[None], None]]] = None,
    ) -> None:
        self.endpoint = endpoint
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.state = "closed"
        self.failures = 0
        self.opened_at = 0.0
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()
        self._on_transition = on_transition

    async def _transition(self, new_state: str) -> None:
        old = self.state
        if old == new_state:
            return
        self.state = new_state
        callback = self._on_transition
        if callback:
            result = callback(old, new_state)
            if asyncio.iscoroutine(result):
                await result

    async def allow(self) -> None:
        async with self._lock:
            if self.state == "open":
                if monotonic() - self.opened_at < self.cooldown:
                    raise CircuitOpenError(f"circuit open for {self.endpoint}")
                self._half_open_in_flight = True
                await self._transition("half-open")
            elif self.state == "half-open" and self._half_open_in_flight:
                raise CircuitOpenError(f"half-open probe in flight for {self.endpoint}")
            elif self.state == "half-open":
                self._half_open_in_flight = True

    async def success(self) -> None:
        async with self._lock:
            self.failures = 0
            self._half_open_in_flight = False
            await self._transition("closed")

    async def failure(self) -> None:
        async with self._lock:
            self._half_open_in_flight = False
            self.failures += 1
            if self.state == "half-open" or self.failures >= self.failure_threshold:
                self.opened_at = monotonic()
                await self._transition("open")

    def snapshot(self) -> BreakerSnapshot:
        return BreakerSnapshot(self.endpoint, self.state, self.failures)
