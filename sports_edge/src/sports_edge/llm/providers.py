"""Additional LLM provider adapters (OpenAI, xAI, Google), all shadow-only.

Model IDs and prices are configuration: verify them in official docs and
log every change. These adapters are UNTESTED against the live APIs from the
build environment (egress blocked). Every provider receives the identical
evidence bundle; none sees another's answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx


@dataclass
class OpenAICompatibleProvider:
    """OpenAI Chat Completions shape; xAI exposes the same shape at its own base URL."""

    name: str  # "openai" | "xai"
    api_key: str
    model_id: str
    base_url: str  # e.g. https://api.openai.com/v1 or https://api.x.ai/v1
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    max_tokens: int = 600

    async def complete(self, system: str, user: str, timeout_s: float) -> tuple[str, Decimal]:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(f"{self.base_url}/chat/completions",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": self.model_id, "max_tokens": self.max_tokens,
                                   "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user}]})
            r.raise_for_status()
            body = r.json()
        text = body["choices"][0]["message"]["content"] or ""
        u = body.get("usage", {})
        cost = (Decimal(u.get("prompt_tokens", 0)) * self.input_usd_per_mtok
                + Decimal(u.get("completion_tokens", 0)) * self.output_usd_per_mtok) / 1_000_000
        return text, cost


@dataclass
class GeminiProvider:
    api_key: str
    model_id: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    name: str = "google"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    async def complete(self, system: str, user: str, timeout_s: float) -> tuple[str, Decimal]:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(f"{self.base_url}/models/{self.model_id}:generateContent",
                             headers={"x-goog-api-key": self.api_key},
                             json={"systemInstruction": {"parts": [{"text": system}]},
                                   "contents": [{"role": "user", "parts": [{"text": user}]}]})
            r.raise_for_status()
            body = r.json()
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        u = body.get("usageMetadata", {})
        cost = (Decimal(u.get("promptTokenCount", 0)) * self.input_usd_per_mtok
                + Decimal(u.get("candidatesTokenCount", 0)) * self.output_usd_per_mtok) / 1_000_000
        return text, cost
