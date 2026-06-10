"""
FreeLLMRouter — failover across free, OpenAI-compatible providers.

Usage mirrors a normal chat-completions client, but a single call may try several
providers in turn until one succeeds:

    router = FreeLLMRouter()
    result = await router.chat_completion(
        messages=[{"role": "user", "content": "Summarize: ..."}],
        tier="smart",
    )
    print(result["text"], "via", result["provider"])

The return shape matches OperatorOS's existing OpenRouter client, so it can be
dropped in as a replacement:
    {text, model, provider, tokens:{prompt,completion,total}, latency_ms, cost_usd}
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from .health import CircuitBreaker
from .providers import Provider, Tier, available_providers
from .ratelimit import TokenBucket

logger = logging.getLogger("free_llm_router")

# Map domain task types (OperatorOS / DragonScope vocabulary) onto the two tiers.
TASK_TIER: Dict[str, Tier] = {
    "factual": "fast",
    "classification": "fast",
    "bulk": "fast",
    "sentiment": "fast",
    "advisory": "smart",
    "computation": "smart",
    "drafting": "smart",
    "summarization": "smart",
    "briefing": "smart",
}


class AllProvidersFailed(RuntimeError):
    """Raised when every eligible provider was skipped or errored."""


@dataclass
class ProviderStats:
    """Live signals the ordering policy can use to rank a provider."""

    provider: Provider
    circuit_state: str        # "closed" | "open" | "half_open"
    tokens_available: bool    # has an RPM token right now
    day_count: int            # requests already spent today
    day_limit: Optional[int]  # documented RPD, or None
    last_latency_ms: float    # most recent successful round-trip (0 if none yet)


# ── Default ordering policy ─────────────────────────────────────────────────────
# Static priority only. This is the seam where smarter, health-aware ranking lives
# — see OrderFn and the note in chat_completion().
def default_order(stats: List[ProviderStats]) -> List[Provider]:
    return [s.provider for s in sorted(stats, key=lambda s: s.provider.priority)]


OrderFn = Callable[[List[ProviderStats]], List[Provider]]


class FreeLLMRouter:
    def __init__(
        self,
        providers: Optional[List[Provider]] = None,
        *,
        order_fn: OrderFn = default_order,
        monotonic: Callable[[], float] = time.monotonic,
        request_timeout: float = 45.0,
    ) -> None:
        self._providers = providers if providers is not None else available_providers()
        if not self._providers:
            logger.warning(
                "FreeLLMRouter has no providers — set at least one of "
                "GROQ_API_KEY / CEREBRAS_API_KEY / GOOGLE_AI_STUDIO_API_KEY / "
                "MISTRAL_API_KEY / OPENROUTER_API_KEY"
            )
        self._order_fn = order_fn
        self._timeout = request_timeout
        self._buckets = {p.name: TokenBucket(p.rpm, monotonic=monotonic) for p in self._providers}
        self._breakers = {p.name: CircuitBreaker(monotonic=monotonic) for p in self._providers}
        self._last_latency: Dict[str, float] = {p.name: 0.0 for p in self._providers}
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=10.0))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── snapshot for the ordering policy ────────────────────────────────────
    async def _snapshot(self) -> List[ProviderStats]:
        stats: List[ProviderStats] = []
        for p in self._providers:
            bucket = self._buckets[p.name]
            stats.append(
                ProviderStats(
                    provider=p,
                    circuit_state=self._breakers[p.name].state.value,
                    tokens_available=(await bucket.seconds_until_token()) == 0.0,
                    day_count=bucket.day_count,
                    day_limit=p.rpd,
                    last_latency_ms=self._last_latency[p.name],
                )
            )
        return stats

    # ── main entry point ─────────────────────────────────────────────────────
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        *,
        tier: Optional[Tier] = None,
        task_type: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> Dict[str, Any]:
        resolved_tier: Tier = tier or TASK_TIER.get(task_type or "", "smart")

        ordered = self._order_fn(await self._snapshot())
        attempted: List[str] = []
        last_error: Optional[Exception] = None

        for provider in ordered:
            model = provider.model_for(resolved_tier)
            if model is None:
                continue

            breaker = self._breakers[provider.name]
            bucket = self._buckets[provider.name]

            if not await breaker.allow():
                continue
            if provider.rpd is not None and bucket.day_count >= provider.rpd:
                continue
            if not await bucket.try_acquire():
                continue  # rate-limited this minute; let the next provider take it

            attempted.append(provider.name)
            try:
                result = await self._call(provider, model, messages, temperature, max_tokens)
                await breaker.record_success()
                self._last_latency[provider.name] = result["latency_ms"]
                logger.info(
                    "free-llm: %s/%s ok tokens=%d latency=%.0fms",
                    provider.name, model, result["tokens"]["total"], result["latency_ms"],
                )
                return result
            except Exception as exc:  # noqa: BLE001 — any failure => try next provider
                last_error = exc
                await breaker.record_failure()
                logger.warning("free-llm: %s failed (%s) — failing over", provider.name, exc)
                continue

        raise AllProvidersFailed(
            f"No free provider served the request (tried: {attempted or 'none eligible'}). "
            f"Last error: {last_error}"
        )

    async def _call(
        self,
        provider: Provider,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        if provider.referer:
            headers["HTTP-Referer"] = provider.referer
            headers["X-Title"] = "free-llm-router"
        headers.update(provider.extra_headers)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        client = await self._http()
        start = time.monotonic()
        resp = await client.post(
            f"{provider.base_url}/chat/completions", json=payload, headers=headers
        )
        latency_ms = (time.monotonic() - start) * 1000
        resp.raise_for_status()
        data = resp.json()

        # OpenRouter (and some others) return HTTP 200 with an error body when
        # the upstream free model is rate-limited — surface it so failover runs.
        if "error" in data and "choices" not in data:
            err = data["error"]
            raise RuntimeError(f"provider error {err.get('code')}: {err.get('message')}")

        choice = data["choices"][0]
        usage = data.get("usage", {}) or {}
        prompt_t = usage.get("prompt_tokens", 0)
        completion_t = usage.get("completion_tokens", 0)
        total_t = usage.get("total_tokens", prompt_t + completion_t)

        return {
            "text": choice["message"]["content"],
            "model": data.get("model", model),
            "provider": provider.name,
            "tokens": {"prompt": prompt_t, "completion": completion_t, "total": total_t},
            "latency_ms": round(latency_ms, 2),
            "cost_usd": 0.0,  # free tier — kept for drop-in contract compatibility
        }

    # ── convenience helper used by classification-style callers ──────────────
    async def quick_classify(self, text: str, categories: List[str]) -> str:
        cats = ", ".join(categories)
        result = await self.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Classifier. Reply with EXACTLY one of: {cats}. "
                        "No explanation, no punctuation."
                    ),
                },
                {"role": "user", "content": text},
            ],
            tier="fast",
            temperature=0.0,
            max_tokens=16,
        )
        raw = result["text"].strip().lower()
        for cat in categories:
            if cat.lower() in raw:
                return cat
        return categories[0]
