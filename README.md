# free-llm-router

One failover router across **free-tier, OpenAI-compatible** model providers,
shipped as **three sibling ports** (Python, browser/Next TypeScript, Node ESM)
sharing the same provider registry, tier model, token-bucket rate limiting,
circuit breaker, and failover — one per target runtime.

Default provider order: **Groq → Cerebras → Google AI Studio → Mistral → OpenRouter**.
Provider availability, quotas, and pricing can change; confirm current terms before
depending on a free tier. Override the order with a custom policy when needed.

## Why twins, not one package

Consuming apps span multiple languages and separate repos, so there is no
single runtime to share. The ports are kept behaviorally identical by hand.
Edit `python/free_llm_router/`, `typescript/free-llm-router/`, and
`node/free-llm-router.mjs` together.

## Layout

```
python/free_llm_router/      canonical Python package
  providers.py   registry (tiers fast|smart, rpm/rpd, priority)
  ratelimit.py   async token bucket (RPM) + daily counter (RPD)
  health.py      circuit breaker (one probe in half-open)
  router.py      FreeLLMRouter.chat_completion + failover loop
  policy.py      smart_order() — YOUR ordering policy hook (see TODO)
typescript/free-llm-router/  canonical browser/Next TS twin (server-only)
node/free-llm-router.mjs     Node ESM port (for plain-JS Express servers)
```

The three ports are kept behaviorally identical by hand — edit together.

## Security: keep keys out of browser bundles

Browser SPAs are 100% client-side. Any API key bundled there is visible in
DevTools and will be scraped, getting the free tier banned, which is exactly
the abuse the upstream resource list warns against. These implementations are
intended for server-side use. A browser application should call a server that
runs the router instead of embedding provider credentials.

## Configuration

Set any subset of these env vars; the router only uses providers whose key exists:

```
FREE_LLM_ENABLED=true
GROQ_API_KEY=
CEREBRAS_API_KEY=
GOOGLE_AI_STUDIO_API_KEY=
MISTRAL_API_KEY=
OPENROUTER_API_KEY=
```
