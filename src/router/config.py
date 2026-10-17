from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

import yaml


@dataclass(frozen=True)
class PoolConfig:
    name: str
    concurrency: int
    queue_size: int
    token_budget: int
    model: str
    fallback_model: str


@dataclass(frozen=True)
class TenantConfig:
    concurrency: int
    queue_size: int
    token_budget: int


@dataclass(frozen=True)
class RouterConfig:
    pools: dict[str, PoolConfig]
    tenants: dict[str, TenantConfig]
    skills: dict[str, str]

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> RouterConfig:
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
        pools = {
            name: PoolConfig(name=name, **values) for name, values in raw.get("pools", {}).items()
        }
        default = TenantConfig(
            **raw.get("tenants", {}).get(
                "default",
                {
                    "concurrency": 2,
                    "queue_size": 8,
                    "token_budget": 20000,
                },
            )
        )
        tenants = {name: TenantConfig(**values) for name, values in raw.get("tenants", {}).items()}
        tenants.setdefault("default", default)
        return cls(pools=pools, tenants=tenants, skills=raw.get("skills", {}))

    def tenant(self, name: str) -> TenantConfig:
        return self.tenants.get(name, self.tenants["default"])

    def pool_for(self, skill: str) -> PoolConfig:
        pool_name = self.skills[skill]
        return self.pools[pool_name]
