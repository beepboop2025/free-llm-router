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
];

export function apiKeyFor(p: Provider): string | undefined {
  return process.env[p.apiKeyEnv] || undefined;
}

export function availableProviders(): Provider[] {
  return REGISTRY.filter((p) => apiKeyFor(p));
}
