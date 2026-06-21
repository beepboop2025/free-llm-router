"""FreeLLMRouter failover loop, ordering, skip rules, and response parsing.

The router injects `monotonic` and exposes `_http()`; we monkeypatch `_http()`
to a FakeHTTPClient so the entire failover loop runs in-process, deterministically,
with zero network and zero API keys.
"""

from __future__ import annotations

import pytest

from free_llm_router.providers import Provider
from free_llm_router.router import (
    AllProvidersFailed,
    FreeLLMRouter,
    ProviderStats,
    TASK_TIER,
    default_order,
)

from conftest import FakeResponse, ok_payload


def provider(name, priority, *, rpm=60, rpd=None) -> Provider:
    return Provider(
        name=name,
        base_url=f"https://{name}.test/v1",
        api_key_env=f"{name.upper()}_KEY",
        models={"fast": f"{name}-fast", "smart": f"{name}-smart"},
        rpm=rpm,
        rpd=rpd,
        priority=priority,
    )


def attach_http(router, client):
    async def _http():
        return client

    router._http = _http  # type: ignore[assignment]
    return client


# ── ordering policy ──────────────────────────────────────────────────────────
def test_default_order_sorts_by_priority_ascending():
    a = provider("a", priority=30)
    b = provider("b", priority=10)
    c = provider("c", priority=20)
    stats = [
        ProviderStats(p, "closed", True, 0, None, 0.0) for p in (a, b, c)
    ]
    ordered = default_order(stats)
    assert [p.name for p in ordered] == ["b", "c", "a"]


# ── tier / task-type resolution ──────────────────────────────────────────────
def test_task_tier_map_classifies_cheap_vs_smart():
    assert TASK_TIER["classification"] == "fast"
    assert TASK_TIER["bulk"] == "fast"
    assert TASK_TIER["advisory"] == "smart"
    assert TASK_TIER["summarization"] == "smart"


@pytest.mark.asyncio
async def test_explicit_tier_selects_that_models_id(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    client = attach_http(router, _client([FakeResponse(ok_payload())]))
    await router.chat_completion([{"role": "user", "content": "hi"}], tier="fast")
    assert client.calls[0]["json"]["model"] == "groq-fast"


@pytest.mark.asyncio
async def test_task_type_resolves_to_tier_when_no_explicit_tier(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    client = attach_http(router, _client([FakeResponse(ok_payload())]))
    await router.chat_completion(
        [{"role": "user", "content": "x"}], task_type="classification"
    )
    assert client.calls[0]["json"]["model"] == "groq-fast"  # classification -> fast


@pytest.mark.asyncio
async def test_unknown_task_type_defaults_to_smart(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    client = attach_http(router, _client([FakeResponse(ok_payload())]))
    await router.chat_completion(
        [{"role": "user", "content": "x"}], task_type="???unknown???"
    )
    assert client.calls[0]["json"]["model"] == "groq-smart"


# ── happy path & response shape ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_successful_call_returns_drop_in_contract_shape(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    payload = ok_payload(content="the answer", model="groq-x", total=42)
    attach_http(router, _client([FakeResponse(payload)]))
    out = await router.chat_completion([{"role": "user", "content": "q"}], tier="smart")
    assert out["text"] == "the answer"
    assert out["provider"] == "groq"
    assert out["model"] == "groq-x"
    assert out["tokens"] == {"prompt": 3, "completion": 4, "total": 42}
    assert out["cost_usd"] == 0.0
    assert out["latency_ms"] >= 0.0


@pytest.mark.asyncio
async def test_total_tokens_derived_when_usage_omits_it(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    payload = {
        "model": "m",
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 6},  # no total_tokens
    }
    attach_http(router, _client([FakeResponse(payload)]))
    out = await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    assert out["tokens"]["total"] == 11


# ── failover behaviour ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fails_over_to_next_provider_on_error(clock):
    first = provider("groq", priority=10)
    second = provider("cerebras", priority=20)
    router = FreeLLMRouter(providers=[first, second], monotonic=clock)
    # first raises, second succeeds
    client = attach_http(
        router,
        _client([RuntimeError("groq down"), FakeResponse(ok_payload(content="ok"))]),
    )
    out = await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    assert out["provider"] == "cerebras"
    assert out["text"] == "ok"
    # both providers were physically attempted, in priority order
    assert [c["url"] for c in client.calls] == [
        "https://groq.test/v1/chat/completions",
        "https://cerebras.test/v1/chat/completions",
    ]


@pytest.mark.asyncio
async def test_all_providers_failed_raises_with_attempt_list(clock):
    a = provider("groq", priority=10)
    b = provider("cerebras", priority=20)
    router = FreeLLMRouter(providers=[a, b], monotonic=clock)
    attach_http(router, _client([RuntimeError("a"), RuntimeError("b")]))
    with pytest.raises(AllProvidersFailed) as ei:
        await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    msg = str(ei.value)
    assert "groq" in msg and "cerebras" in msg


@pytest.mark.asyncio
async def test_provider_lacking_requested_tier_is_skipped(clock):
    # 'partial' offers no smart model → must be skipped, not attempted.
    partial = Provider(
        name="partial",
        base_url="https://partial.test/v1",
        api_key_env="PARTIAL_KEY",
        models={"fast": "only-fast"},  # no 'smart'
        rpm=60,
        rpd=None,
        priority=10,
    )
    full = provider("groq", priority=20)
    router = FreeLLMRouter(providers=[partial, full], monotonic=clock)
    client = attach_http(router, _client([FakeResponse(ok_payload(content="z"))]))
    out = await router.chat_completion([{"role": "user", "content": "q"}], tier="smart")
    assert out["provider"] == "groq"
    # partial was never POSTed to
    assert all("partial" not in c["url"] for c in client.calls)


# ── 200-with-error body (OpenRouter quirk) ───────────────────────────────────
@pytest.mark.asyncio
async def test_http_200_with_error_body_triggers_failover(clock):
    first = provider("openrouter", priority=10)
    second = provider("groq", priority=20)
    router = FreeLLMRouter(providers=[first, second], monotonic=clock)
    err_body = {"error": {"code": 429, "message": "rate limited"}}  # no 'choices'
    attach_http(
        router,
        _client([FakeResponse(err_body), FakeResponse(ok_payload(content="recovered"))]),
    )
    out = await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    assert out["provider"] == "groq"
    assert out["text"] == "recovered"


# ── rate-limit skipping ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rpm_exhaustion_skips_to_next_provider(clock):
    # groq has rpm=1: second call within the same minute must skip to cerebras.
    g = provider("groq", priority=10, rpm=1)
    c = provider("cerebras", priority=20, rpm=60)
    router = FreeLLMRouter(providers=[g, c], monotonic=clock)
    attach_http(
        router,
        _client(
            [
                FakeResponse(ok_payload(content="first")),   # groq
                FakeResponse(ok_payload(content="second")),  # cerebras (groq rate-limited)
            ]
        ),
    )
    a = await router.chat_completion([{"role": "user", "content": "1"}], tier="fast")
    b = await router.chat_completion([{"role": "user", "content": "2"}], tier="fast")
    assert a["provider"] == "groq"
    assert b["provider"] == "cerebras"


@pytest.mark.asyncio
async def test_daily_cap_skips_provider(clock):
    # rpd=1: after one successful request, the daily cap blocks groq entirely.
    g = provider("groq", priority=10, rpm=60, rpd=1)
    c = provider("cerebras", priority=20, rpm=60)
    router = FreeLLMRouter(providers=[g, c], monotonic=clock)
    attach_http(
        router,
        _client(
            [FakeResponse(ok_payload(content="g")), FakeResponse(ok_payload(content="c"))]
        ),
    )
    first = await router.chat_completion([{"role": "user", "content": "1"}], tier="fast")
    second = await router.chat_completion([{"role": "user", "content": "2"}], tier="fast")
    assert first["provider"] == "groq"
    assert second["provider"] == "cerebras"  # groq over daily cap


# ── circuit breaker integration ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_open_circuit_skips_provider_without_calling_it(clock):
    # groq fails 3x (default threshold) → circuit opens → 4th request skips it.
    g = provider("groq", priority=10, rpm=60)
    c = provider("cerebras", priority=20, rpm=60)
    router = FreeLLMRouter(providers=[g, c], monotonic=clock)
    # Script: groq fails 3 times, cerebras succeeds each of those 3, then a 4th
    # request must NOT touch groq (open) and go straight to cerebras.
    attach_http(
        router,
        _client(
            [
                RuntimeError("g1"), FakeResponse(ok_payload(content="c1")),
                RuntimeError("g2"), FakeResponse(ok_payload(content="c2")),
                RuntimeError("g3"), FakeResponse(ok_payload(content="c3")),
                FakeResponse(ok_payload(content="c4")),  # 4th: only cerebras
            ]
        ),
    )
    for _ in range(3):
        await router.chat_completion([{"role": "user", "content": "x"}], tier="fast")
    client = await router._http()
    calls_before = len(client.calls)
    out = await router.chat_completion([{"role": "user", "content": "x"}], tier="fast")
    assert out["provider"] == "cerebras"
    # exactly one new POST (cerebras), groq was skipped pre-flight
    assert len(client.calls) == calls_before + 1
    assert client.calls[-1]["url"].startswith("https://cerebras")


# ── custom ordering hook ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_custom_order_fn_overrides_priority(clock):
    g = provider("groq", priority=10)
    c = provider("cerebras", priority=20)
    # Reverse the static order: prefer the lower-priority provider first.
    def reverse_order(stats):
        return [s.provider for s in sorted(stats, key=lambda s: -s.provider.priority)]

    router = FreeLLMRouter(providers=[g, c], order_fn=reverse_order, monotonic=clock)
    client = attach_http(router, _client([FakeResponse(ok_payload(content="z"))]))
    out = await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    assert out["provider"] == "cerebras"  # reversed order tried cerebras first
    assert client.calls[0]["url"].startswith("https://cerebras")


# ── headers ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_referer_provider_sends_attribution_headers(clock, monkeypatch):
    p = Provider(
        name="openrouter",
        base_url="https://openrouter.test/v1",
        api_key_env="OR_KEY",
        models={"fast": "f", "smart": "s"},
        rpm=60,
        rpd=None,
        priority=10,
        referer="https://example.test/attr",
    )
    monkeypatch.setenv("OR_KEY", "sk-test")
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    client = attach_http(router, _client([FakeResponse(ok_payload())]))
    await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    headers = client.calls[0]["headers"]
    assert headers["HTTP-Referer"] == "https://example.test/attr"
    assert headers["X-Title"] == "free-llm-router"
    assert headers["Authorization"] == "Bearer sk-test"


# ── quick_classify ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_quick_classify_matches_category_case_insensitively(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    attach_http(router, _client([FakeResponse(ok_payload(content="  POSITIVE\n"))]))
    label = await router.quick_classify("great product", ["positive", "negative"])
    assert label == "positive"


@pytest.mark.asyncio
async def test_quick_classify_falls_back_to_first_category_on_no_match(clock):
    p = provider("groq", priority=10)
    router = FreeLLMRouter(providers=[p], monotonic=clock)
    attach_http(router, _client([FakeResponse(ok_payload(content="banana"))]))
    label = await router.quick_classify("???", ["positive", "negative"])
    assert label == "positive"  # unrecognized reply -> first category


# ── no-providers edge case ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_empty_provider_set_raises_with_none_eligible(clock):
    router = FreeLLMRouter(providers=[], monotonic=clock)
    with pytest.raises(AllProvidersFailed) as ei:
        await router.chat_completion([{"role": "user", "content": "q"}], tier="fast")
    assert "none eligible" in str(ei.value)


# ── helper ───────────────────────────────────────────────────────────────────
def _client(handlers):
    from conftest import FakeHTTPClient

    return FakeHTTPClient(handlers)
