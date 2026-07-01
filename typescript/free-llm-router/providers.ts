/**
 * TypeScript twin of the Python `free_llm_router` provider registry.
 *
 * SERVER-ONLY. These entries read API keys from process.env — importing this in a
 * client component would leak keys into the browser bundle. Keep it behind server
 * code (route handlers, server actions, server-only libs).
 *
 * Every provider exposes an OpenAI-compatible POST {baseUrl}/chat/completions, so a
 * single request can fail over across all of them by swapping baseUrl/key/model.
 * Mirrors providers.py — keep the two in sync when editing.
 */

export type Tier = "fast" | "smart";

export interface Provider {
  name: string;
  baseUrl: string;
  apiKeyEnv: string;
  models: Record<Tier, string>;
  rpm: number;
  rpd: number | null;
  priority: number;
  referer?: string;
  /** False = paid; excluded from the default failover chain (opt-in only). */
  free?: boolean;
  /** USD per 1M prompt tokens (0 / omitted for free tiers). */
  costInPer1m?: number;
  /** USD per 1M completion tokens. */
  costOutPer1m?: number;
}

export const REGISTRY: Provider[] = [
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
    // VitalChain already uses GEMINI_API_KEY for the native PDF path; reuse it here.
    apiKeyEnv: "GEMINI_API_KEY",
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
  // ── Paid, opt-in only ──────────────────────────────────────────────────────
  // Kimi (Moonshot AI) — Chinese-origin model. NOT free, but cheap and the best
  // option for Chinese-language work. Excluded from default failover; reached
  // only when a caller prefers it (lang "zh" / china task / preferProvider).
  // Key: platform.moonshot.ai (min $1) → MOONSHOT_API_KEY. Use api.moonshot.cn
  // for China-platform billing. Prices are Jun 2026 cache-miss rates.
  {
    name: "kimi",
    baseUrl: "https://api.moonshot.ai/v1",
    apiKeyEnv: "MOONSHOT_API_KEY",
    models: { fast: "kimi-k2.5", smart: "kimi-k2.6" },
    rpm: 200,
    rpd: null,
    priority: 100,
    free: false,
    costInPer1m: 0.95,
    costOutPer1m: 4.0,
  },
];

export function apiKeyFor(p: Provider): string | undefined {
  return process.env[p.apiKeyEnv] || undefined;
}

export function availableProviders(): Provider[] {
  return REGISTRY.filter((p) => apiKeyFor(p));
}

/** Marginal USD cost of a call; 0 for free providers. */
export function costUsd(p: Provider, promptTokens: number, completionTokens: number): number {
  const cin = ((p.costInPer1m ?? 0) * promptTokens) / 1_000_000;
  const cout = ((p.costOutPer1m ?? 0) * completionTokens) / 1_000_000;
  return Math.round((cin + cout) * 1e6) / 1e6;
}
