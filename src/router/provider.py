from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Optional


class ModelError(RuntimeError):
    pass


class ModelProvider:
    async def generate(
        self, *, model: str, skill: str, payload: Mapping[str, object], tenant: str
    ) -> object:
        raise NotImplementedError


class MockProvider(ModelProvider):
    """Stable provider for local runs and tests; failures are injectable by tenant."""

    def __init__(self, *, delay: float = 0.002, fail_tenants: Optional[set[str]] = None) -> None:
        self.delay = delay
        self.fail_tenants = set(fail_tenants or ())
        self.calls: list[tuple[str, str, str]] = []

    async def generate(
        self, *, model: str, skill: str, payload: Mapping[str, object], tenant: str
    ) -> object:
        self.calls.append((tenant, model, skill))
        await asyncio.sleep(self.delay if model == "mock-primary" else self.delay / 2)
        if tenant in self.fail_tenants and model == "mock-primary":
            raise ModelError(f"injected failure for {tenant}")
        if skill == "summarize":
            text = str(payload.get("text", payload.get("description", "")))
            return {"summary": " ".join(text.split())[:240], "model": model}
        if skill == "classify":
            text = str(payload.get("text", payload.get("description", ""))).lower()
            label = (
                "billing"
                if any(word in text for word in ("invoice", "charge", "payment"))
                else "technical"
            )
            return {"label": label, "model": model}
        return {
            "fields": {
                key: value
                for key, value in payload.items()
                if key in {"id", "priority", "product", "category"}
            },
            "model": model,
        }


class AzureOpenAIProvider(ModelProvider):
    """Optional Azure OpenAI adapter; imported only when explicitly configured."""

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version

    async def generate(
        self, *, model: str, skill: str, payload: Mapping[str, object], tenant: str
    ) -> object:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ModelError("httpx is required for Azure OpenAI") from exc
        prompt = json.dumps({"skill": skill, "payload": payload}, sort_keys=True)
        url = f"{self.endpoint.rstrip('/')}/openai/deployments/{self.deployment}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    url,
                    params={"api-version": self.api_version},
                    headers={"api-key": self.api_key},
                    json={"messages": [{"role": "user", "content": prompt}], "temperature": 0},
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ModelError(f"Azure OpenAI call failed: {exc}") from exc
