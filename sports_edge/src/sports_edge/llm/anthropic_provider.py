"""Anthropic Messages API adapter (first LLM provider).

Model ID and prices come from configuration (``LLM_ANTHROPIC_MODEL``,
``LLM_ANTHROPIC_INPUT_USD_PER_MTOK``, ``LLM_ANTHROPIC_OUTPUT_USD_PER_MTOK``),
checked against official docs and recorded in DATA_SOURCES.md. Disabled
unless an API key is configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx


@dataclass
class AnthropicProvider:
    api_key: str
    model_id: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    max_tokens: int = 600
    name: str = "anthropic"
    base_url: str = "https://api.anthropic.com/v1/messages"

    async def complete(self, system: str, user: str, timeout_s: float) -> tuple[str, Decimal]:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                self.base_url,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.model_id, "max_tokens": self.max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
            )
            r.raise_for_status()
            body = r.json()
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        usage = body.get("usage", {})
        cost = (Decimal(usage.get("input_tokens", 0)) * self.input_usd_per_mtok
                + Decimal(usage.get("output_tokens", 0)) * self.output_usd_per_mtok) / 1_000_000
        return text, cost
