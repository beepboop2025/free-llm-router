"""Client lifecycle (lazy create / close) and the policy placeholder.

No network is performed: we only construct and close the httpx client, never POST.
"""

from __future__ import annotations

import httpx
import pytest

from free_llm_router.policy import smart_order
from free_llm_router.providers import Provider
from free_llm_router.router import FreeLLMRouter, ProviderStats, default_order


def _provider(name, priority):
    return Provider(
        name=name,
        base_url=f"https://{name}.test/v1",
        api_key_env=f"{name.upper()}_KEY",
        models={"fast": "f", "smart": "s"},
        rpm=60,
        rpd=None,
        priority=priority,
    )


@pytest.mark.asyncio
async def test_http_lazily_creates_a_single_client(clock):
    router = FreeLLMRouter(providers=[_provider("groq", 10)], monotonic=clock)
    c1 = await router._http()
    c2 = await router._http()
    assert isinstance(c1, httpx.AsyncClient)
    assert c1 is c2  # cached, not recreated each call
    await router.close()


@pytest.mark.asyncio
async def test_close_disposes_client_and_recreates_on_next_use(clock):
    router = FreeLLMRouter(providers=[_provider("groq", 10)], monotonic=clock)
    first = await router._http()
    await router.close()
    assert first.is_closed
    second = await router._http()  # a fresh, open client
    assert second is not first
    assert not second.is_closed
    await router.close()


@pytest.mark.asyncio
async def test_close_is_idempotent(clock):
    router = FreeLLMRouter(providers=[_provider("groq", 10)], monotonic=clock)
    await router._http()
    await router.close()
    await router.close()  # second close must not raise


def test_smart_order_placeholder_delegates_to_default_priority_sort():
    a = _provider("a", 30)
    b = _provider("b", 10)
    stats = [ProviderStats(p, "closed", True, 0, None, 0.0) for p in (a, b)]
    # The shipped policy is a placeholder that mirrors default_order (priority sort).
    assert [p.name for p in smart_order(stats)] == [
        p.name for p in default_order(stats)
    ]
    assert [p.name for p in smart_order(stats)] == ["b", "a"]
