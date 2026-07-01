# free-llm-router

One failover router across **perpetually-free, OpenAI-compatible** LLM providers,
shipped as **three sibling ports** (Python, browser/Next TypeScript, Node ESM)
sharing the same provider registry, tier model, token-bucket rate limiting,
circuit breaker, and failover — one per target runtime.

Providers (free tiers only): **Groq → Cerebras → Google AI Studio → Mistral → OpenRouter**
(static priority order; override with a custom ordering policy).

Plus one **paid, opt-in** provider — **Kimi (Moonshot AI)**, a Chinese-origin model
for Chinese-language work. It is deliberately **excluded from the default failover
chain** so normal traffic never spends money; it's reached only when a caller
explicitly prefers it (see *Chinese-language routing* below).

## Why twins, not one package

The consuming apps span two languages and separate repos, so there's no single
runtime to share. The two packages are kept behaviorally identical by hand —
edit both `python/free_llm_router/` and `vitalchain/src/lib/free-llm-router/`
together.

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
sync.sh          vendors all three ports into each consuming app
```

The three ports are kept behaviorally identical by hand — edit together.

## How it's wired into each app

| App | Language | Integration | Direction |
|-----|----------|-------------|-----------|
| **OperatorOS** | Py (FastAPI) | `OpenRouterClient.chat_completion` routes cheap tasks (classification/factual/bulk) free-first; advisory/drafting paid-first with free fallback | drop-in, all services benefit |
| **social_scraper** | Py (Celery) | LLM sentiment tier added above FinBERT→VADER in `processors/sentiment.py` | LLM top tier, rule-based fallback |
| **DragonScope** | Py (FastAPI) | `NlpEngine.summarize_async` does abstractive summaries via router; `/nlp/analyze` awaits it | LLM with extractive fallback |
| **VitalChain** | TS (Next.js) | `src/lib/intel/llm.ts` text path uses the router with multi-provider failover; PDF path stays native Gemini/Claude | free-first |
| **DragonScope UI** | TS (Vite SPA) | calls `/api/llm/chat` on its authed Express server, which runs the Node port (`server/lib/free-llm-router.mjs`). Keys server-side; `api.llmChat()` in the SPA | ✅ |
| **LiquiFi** | TS (Vite SPA) | ❌ empty working tree — integration pending | blocked |

## Security: never put keys in a browser bundle

Vite SPAs (LiquiFi, DragonScope UI) are 100% client-side. Any API key bundled
there is visible in DevTools and will be scraped — getting the free tier banned,
which is exactly the abuse the upstream resource list warns against. The router
is **server-only** in every app. SPAs must call a server (DragonScope's Python
backend, or a serverless function for LiquiFi).

## Configuration

Set any subset of these env vars; the router only uses providers whose key exists:

```
FREE_LLM_ENABLED=true
GROQ_API_KEY=
CEREBRAS_API_KEY=
GOOGLE_AI_STUDIO_API_KEY=   # VitalChain reuses GEMINI_API_KEY instead
MISTRAL_API_KEY=
OPENROUTER_API_KEY=
MOONSHOT_API_KEY=           # paid, opt-in — Kimi/Moonshot (platform.moonshot.ai, min $1)
```

## Chinese-language routing (Kimi)

Kimi only runs when you ask for it — three equivalent ways, all with **free-tier
fallback** if the key is missing or Kimi is rate-limited/down:

```python
# Python — any of:
await router.chat_completion(msgs, lang="zh")                 # explicit language
await router.chat_completion(msgs, task_type="china_intel")   # china task → kimi
await router.chat_completion(msgs, task_type="translation")   # zh_summarization / zh_classification too
await router.chat_completion(msgs, prefer_provider="kimi")    # force it for any task
```

```ts
// TypeScript / Node — same options:
await router.chatCompletion(msgs, { lang: "zh" });
await router.chatCompletion(msgs, { taskType: "china_intel" });
await router.chatCompletion(msgs, { preferProvider: "kimi" });
```

Semantics:
- **Default (no hint):** only free providers are tried — Kimi is never touched, `cost_usd` stays 0.
- **Preferred:** Kimi is tried **first**, then the free chain as fallback. `cost_usd`
  now reflects real Moonshot per-token cost (kimi-k2.6: $0.95/M in, $4.00/M out).
- **`prefer_provider="groq"`** (or any free name) just reorders the free chain —
  it never pulls the paid provider in.

Use it for: CN-source scraping/summaries (social_scraper, palimpsest), translation,
China market/regulatory context (drug-price-observatory). For bulk cost offload,
prefer Kimi on `bulk`/`classification` calls — but note that's a paid call, so it
trades free-tier quota for a small spend.

## After editing the Python package

```
bash sync.sh   # re-vendor into operatoros / social_scraper / DragonScope backends
```
