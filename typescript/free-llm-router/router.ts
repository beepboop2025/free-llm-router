/**
 * TypeScript twin of free_llm_router.router — failover across free, OpenAI-compatible
 * providers. SERVER-ONLY (reads API keys from process.env).
 *
 * Note vs the Python version: JS runs single-threaded on one event loop, so the
 * token bucket and circuit breaker need no locks — a synchronous check-then-mutate
 * can't be interleaved by another "thread". That removes the TOCTOU class of bugs
 * the Python TokenBucket guards against with asyncio.Lock.
 */

import {
  type Provider,
  type Tier,
  apiKeyFor,
  availableProviders,
} from "./providers";

const TASK_TIER: Record<string, Tier> = {
  classification: "fast",
  factual: "fast",
  bulk: "fast",
  sentiment: "fast",
  advisory: "smart",
  drafting: "smart",
  summarization: "smart",
  briefing: "smart",
};

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatResult {
  text: string;
  model: string;
  provider: string;
  tokens: { prompt: number; completion: number; total: number };
  latencyMs: number;
  costUsd: number;
}

// ── token bucket (RPM) + daily counter (RPD) ────────────────────────────────────
class TokenBucket {
  private tokens: number;
  private last: number;
  private readonly capacity: number;
  private readonly refillPerSec: number;
  dayCount = 0;

  constructor(rpm: number) {
    this.capacity = Math.max(rpm, 1);
    this.tokens = this.capacity;
    this.refillPerSec = Math.max(rpm, 1) / 60;
    this.last = Date.now();
  }

  private refill() {
    const now = Date.now();
    const elapsed = (now - this.last) / 1000;
    if (elapsed > 0) {
      this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.refillPerSec);
      this.last = now;
    }
  }

  tryAcquire(): boolean {
    this.refill();
    if (this.tokens >= 1) {
      this.tokens -= 1;
      this.dayCount += 1;
      return true;
    }
    return false;
  }

  hasToken(): boolean {
    this.refill();
    return this.tokens >= 1;
  }
}

// ── circuit breaker (one probe in half-open) ────────────────────────────────────
type State = "closed" | "open" | "half_open";
class CircuitBreaker {
  private state: State = "closed";
  private failures = 0;
  private openedAt = 0;
  private probeInFlight = false;

  constructor(
    private readonly threshold = 3,
    private readonly cooldownMs = 30_000,
  ) {}

  allow(): boolean {
    if (this.state === "closed") return true;
    if (this.state === "open") {
      if (Date.now() - this.openedAt >= this.cooldownMs) {
        this.state = "half_open";
        this.probeInFlight = true;
        return true; // single probe
      }
      return false;
    }
    // half_open: only the in-flight probe proceeds
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

  get circuitState(): State {
    return this.state;
  }
}

export interface ProviderStats {
  provider: Provider;
  circuitState: State;
  tokensAvailable: boolean;
  dayCount: number;
  dayLimit: number | null;
  lastLatencyMs: number;
}

export type OrderFn = (stats: ProviderStats[]) => Provider[];

// Default: static priority. Mirrors policy.py — replace with health/quota-aware
// ranking if you want smarter ordering (see the Python policy.smart_order note).
export const defaultOrder: OrderFn = (stats) =>
  [...stats].sort((a, b) => a.provider.priority - b.provider.priority).map((s) => s.provider);

export class FreeLLMRouter {
  private readonly providers: Provider[];
  private readonly buckets = new Map<string, TokenBucket>();
  private readonly breakers = new Map<string, CircuitBreaker>();
  private readonly lastLatency = new Map<string, number>();

  constructor(
    private readonly orderFn: OrderFn = defaultOrder,
    private readonly timeoutMs = 30_000,
    providers?: Provider[],
  ) {
    this.providers = providers ?? availableProviders();
    for (const p of this.providers) {
      this.buckets.set(p.name, new TokenBucket(p.rpm));
      this.breakers.set(p.name, new CircuitBreaker());
      this.lastLatency.set(p.name, 0);
    }
  }

  get hasProviders(): boolean {
    return this.providers.length > 0;
  }

  private snapshot(): ProviderStats[] {
    return this.providers.map((p) => ({
      provider: p,
      circuitState: this.breakers.get(p.name)!.circuitState,
      tokensAvailable: this.buckets.get(p.name)!.hasToken(),
      dayCount: this.buckets.get(p.name)!.dayCount,
      dayLimit: p.rpd,
      lastLatencyMs: this.lastLatency.get(p.name)!,
    }));
  }

  async chatCompletion(
    messages: ChatMessage[],
    opts: { tier?: Tier; taskType?: string; temperature?: number; maxTokens?: number } = {},
  ): Promise<ChatResult> {
    const tier: Tier = opts.tier ?? TASK_TIER[opts.taskType ?? ""] ?? "smart";
    const ordered = this.orderFn(this.snapshot());
    const attempted: string[] = [];
    let lastErr: unknown = null;

    for (const provider of ordered) {
      const model = provider.models[tier];
      if (!model) continue;
      const bucket = this.buckets.get(provider.name)!;
      const breaker = this.breakers.get(provider.name)!;

      if (!breaker.allow()) continue;
      if (provider.rpd !== null && bucket.dayCount >= provider.rpd) continue;
      if (!bucket.tryAcquire()) continue;

      attempted.push(provider.name);
      try {
        const result = await this.call(provider, model, messages, opts);
        breaker.recordSuccess();
        this.lastLatency.set(provider.name, result.latencyMs);
        return result;
      } catch (err) {
        lastErr = err;
        breaker.recordFailure();
        continue;
      }
    }
    throw new Error(
      `No free provider served the request (tried: ${attempted.join(", ") || "none"}). Last error: ${String(lastErr)}`,
    );
  }

  private async call(
    provider: Provider,
    model: string,
    messages: ChatMessage[],
    opts: { temperature?: number; maxTokens?: number },
  ): Promise<ChatResult> {
    const headers: Record<string, string> = {
      authorization: `Bearer ${apiKeyFor(provider)!}`,
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
      const data = (await res.json()) as {
        model?: string;
        choices: { message: { content: string } }[];
        usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
      };
      const u = data.usage ?? {};
      const prompt = u.prompt_tokens ?? 0;
      const completion = u.completion_tokens ?? 0;
      return {
        text: data.choices[0]?.message?.content ?? "",
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

// Module-level singleton so buckets/breakers persist across calls within a server
// process (Next.js reuses the module between requests in the same worker).
let _router: FreeLLMRouter | null = null;
export function getFreeRouter(): FreeLLMRouter {
  if (!_router) _router = new FreeLLMRouter();
  return _router;
}
