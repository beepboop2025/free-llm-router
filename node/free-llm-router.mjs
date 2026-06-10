/**
 * free-llm-router — Node ESM port (server-only).
 *
 * Behavioral twin of python/free_llm_router and typescript/free-llm-router.
 * For plain-JS Node servers (e.g. Express). Keep in sync with the other two.
 *
 * SERVER-ONLY: reads API keys from process.env. Never bundle into a browser.
 */

// ── provider registry (mirror providers.py / providers.ts) ──────────────────────
export const REGISTRY = [
  {
    name: "groq",
    baseUrl: "https://api.groq.com/openai/v1",
    apiKeyEnv: "GROQ_API_KEY",
    models: { fast: "llama-3.1-8b-instant", smart: "llama-3.3-70b-versatile" },
    rpm: 30,
    rpd: 14_400,
    priority: 10,
  },
  {
    name: "cerebras",
    baseUrl: "https://api.cerebras.ai/v1",
    apiKeyEnv: "CEREBRAS_API_KEY",
    models: { fast: "llama3.1-8b", smart: "llama-3.3-70b" },
    rpm: 30,
    rpd: 14_400,
    priority: 20,
  },
  {
    name: "google_ai_studio",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    apiKeyEnv: "GOOGLE_AI_STUDIO_API_KEY",
    models: { fast: "gemini-2.0-flash-lite", smart: "gemini-2.0-flash" },
    rpm: 15,
    rpd: 1_500,
    priority: 30,
  },
  {
    name: "mistral",
    baseUrl: "https://api.mistral.ai/v1",
    apiKeyEnv: "MISTRAL_API_KEY",
    models: { fast: "open-mistral-nemo", smart: "mistral-small-latest" },
    rpm: 60,
    rpd: null,
    priority: 40,
  },
  {
    name: "openrouter",
    baseUrl: "https://openrouter.ai/api/v1",
    apiKeyEnv: "OPENROUTER_API_KEY",
    models: {
      fast: "meta-llama/llama-3.3-70b-instruct:free",
      smart: "deepseek/deepseek-r1:free",
    },
    rpm: 20,
    rpd: 50,
    priority: 50,
    referer: "https://github.com/cheahjs/free-llm-api-resources",
  },
];

const apiKeyFor = (p) => process.env[p.apiKeyEnv] || undefined;
export const availableProviders = () => REGISTRY.filter(apiKeyFor);

const TASK_TIER = {
  classification: "fast",
  factual: "fast",
  bulk: "fast",
  sentiment: "fast",
  advisory: "smart",
  drafting: "smart",
  summarization: "smart",
  briefing: "smart",
};

// ── token bucket (no locks — single-threaded event loop) ────────────────────────
class TokenBucket {
  constructor(rpm) {
    this.capacity = Math.max(rpm, 1);
    this.tokens = this.capacity;
    this.refillPerSec = Math.max(rpm, 1) / 60;
    this.last = Date.now();
    this.dayCount = 0;
  }
  #refill() {
    const now = Date.now();
    const elapsed = (now - this.last) / 1000;
    if (elapsed > 0) {
      this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.refillPerSec);
      this.last = now;
    }
  }
  tryAcquire() {
    this.#refill();
    if (this.tokens >= 1) {
      this.tokens -= 1;
      this.dayCount += 1;
      return true;
    }
    return false;
  }
  hasToken() {
    this.#refill();
    return this.tokens >= 1;
  }
}

// ── circuit breaker (one probe in half-open) ────────────────────────────────────
class CircuitBreaker {
  constructor(threshold = 3, cooldownMs = 30_000) {
    this.threshold = threshold;
    this.cooldownMs = cooldownMs;
    this.state = "closed";
    this.failures = 0;
    this.openedAt = 0;
    this.probeInFlight = false;
  }
  allow() {
    if (this.state === "closed") return true;
    if (this.state === "open") {
      if (Date.now() - this.openedAt >= this.cooldownMs) {
        this.state = "half_open";
        this.probeInFlight = true;
        return true;
      }
      return false;
    }
    if (!this.probeInFlight) {
      this.probeInFlight = true;
      return true;
    }
    return false;
  }
  recordSuccess() {
    this.failures = 0;
    this.probeInFlight = false;
    this.state = "closed";
  }
  recordFailure() {
    this.probeInFlight = false;
    if (this.state === "half_open") {
      this.state = "open";
      this.openedAt = Date.now();
      return;
    }
    this.failures += 1;
    if (this.failures >= this.threshold) {
      this.state = "open";
      this.openedAt = Date.now();
    }
  }
}

export const defaultOrder = (stats) =>
  [...stats].sort((a, b) => a.provider.priority - b.provider.priority).map((s) => s.provider);

export class FreeLLMRouter {
  constructor({ orderFn = defaultOrder, timeoutMs = 30_000, providers } = {}) {
    this.orderFn = orderFn;
    this.timeoutMs = timeoutMs;
    this.providers = providers ?? availableProviders();
    this.buckets = new Map();
    this.breakers = new Map();
    this.lastLatency = new Map();
    for (const p of this.providers) {
      this.buckets.set(p.name, new TokenBucket(p.rpm));
      this.breakers.set(p.name, new CircuitBreaker());
      this.lastLatency.set(p.name, 0);
    }
  }

  get hasProviders() {
    return this.providers.length > 0;
  }

  #snapshot() {
    return this.providers.map((p) => ({
      provider: p,
      circuitState: this.breakers.get(p.name).state,
      tokensAvailable: this.buckets.get(p.name).hasToken(),
      dayCount: this.buckets.get(p.name).dayCount,
      dayLimit: p.rpd,
      lastLatencyMs: this.lastLatency.get(p.name),
    }));
  }

  async chatCompletion(messages, opts = {}) {
    const tier = opts.tier ?? TASK_TIER[opts.taskType ?? ""] ?? "smart";
    const ordered = this.orderFn(this.#snapshot());
    const attempted = [];
    let lastErr = null;

    for (const provider of ordered) {
      const model = provider.models[tier];
      if (!model) continue;
      const bucket = this.buckets.get(provider.name);
      const breaker = this.breakers.get(provider.name);

      if (!breaker.allow()) continue;
      if (provider.rpd !== null && bucket.dayCount >= provider.rpd) continue;
      if (!bucket.tryAcquire()) continue;

      attempted.push(provider.name);
      try {
        const result = await this.#call(provider, model, messages, opts);
        breaker.recordSuccess();
        this.lastLatency.set(provider.name, result.latencyMs);
        return result;
      } catch (err) {
        lastErr = err;
        breaker.recordFailure();
      }
    }
    throw new Error(
      `No free provider served the request (tried: ${attempted.join(", ") || "none"}). Last error: ${String(lastErr)}`,
    );
  }

  async #call(provider, model, messages, opts) {
    const headers = {
      authorization: `Bearer ${apiKeyFor(provider)}`,
      "content-type": "application/json",
    };
    if (provider.referer) {
      headers["http-referer"] = provider.referer;
      headers["x-title"] = "free-llm-router";
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), this.timeoutMs);
    const start = Date.now();
    try {
      const res = await fetch(`${provider.baseUrl}/chat/completions`, {
        method: "POST",
        signal: ctrl.signal,
        headers,
        body: JSON.stringify({
          model,
          messages,
          temperature: opts.temperature ?? 0.3,
          max_tokens: opts.maxTokens ?? 2048,
        }),
      });
      const latencyMs = Date.now() - start;
      if (!res.ok) throw new Error(`${provider.name} HTTP ${res.status}`);
      const data = await res.json();
      const u = data.usage ?? {};
      const prompt = u.prompt_tokens ?? 0;
      const completion = u.completion_tokens ?? 0;
      return {
        text: data.choices?.[0]?.message?.content ?? "",
        model: data.model ?? model,
        provider: provider.name,
        tokens: { prompt, completion, total: u.total_tokens ?? prompt + completion },
        latencyMs,
        costUsd: 0,
      };
    } finally {
      clearTimeout(t);
    }
  }
}

let _router = null;
export function getFreeRouter() {
  if (!_router) _router = new FreeLLMRouter();
  return _router;
}
