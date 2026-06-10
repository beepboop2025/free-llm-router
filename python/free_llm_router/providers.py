"""
Registry of perpetually-free, OpenAI-compatible LLM providers.

Every provider here exposes a ``POST {base_url}/chat/completions`` endpoint that
accepts the OpenAI request schema. That uniformity is what lets a single client
body talk to all of them — only ``base_url``, the API key, and the model id change.

We model two logical *tiers* instead of hard-coding model names at call sites:

    "fast"  – small, low-latency model for classification / bulk work
    "smart" – larger model for drafting / reasoning / summarization

Each provider maps the tiers to a concrete model it offers for free. Callers ask
for a tier; the router resolves it per-provider during failover.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

Tier = str  # "fast" | "smart"


@dataclass(frozen=True)
class Provider:
    """A single free LLM provider and its free-tier characteristics."""

    name: str
    base_url: str
    api_key_env: str           # env var holding the key
    models: Dict[Tier, str]    # tier -> concrete model id offered for free
    rpm: int                   # documented free-tier requests per minute
    rpd: Optional[int]         # documented free-tier requests per day (None = unknown)
    priority: int              # tie-breaker; lower = generally preferred
    referer: str = ""          # OpenRouter wants HTTP-Referer/X-Title for free tier
    extra_headers: Dict[str, str] = field(default_factory=dict)

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env) or None

    def model_for(self, tier: Tier) -> Optional[str]:
        return self.models.get(tier)


# ── The registry (perpetually-free tiers only — no trial-credit providers) ──────
#
# Limits are the documented free-tier numbers at time of writing; they drift, so
# treat them as hints for the rate limiter rather than guarantees. Sources:
# github.com/cheahjs/free-llm-api-resources

REGISTRY: List[Provider] = [
    Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        models={"fast": "llama-3.1-8b-instant", "smart": "llama-3.3-70b-versatile"},
        rpm=30,
        rpd=14_400,
        priority=10,  # fastest inference of the free tiers
    ),
    Provider(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        models={"fast": "llama3.1-8b", "smart": "llama-3.3-70b"},
        rpm=30,
        rpd=14_400,
        priority=20,
    ),
    Provider(
        name="google_ai_studio",
        # Google exposes an OpenAI-compatible shim under /v1beta/openai
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GOOGLE_AI_STUDIO_API_KEY",
        models={"fast": "gemini-2.0-flash-lite", "smart": "gemini-2.0-flash"},
        rpm=15,
        rpd=1_500,
        priority=30,  # generous token quota, strong quality
    ),
    Provider(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        models={"fast": "open-mistral-nemo", "smart": "mistral-small-latest"},
        rpm=60,
        rpd=None,
        priority=40,
    ),
    Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        # ":free" suffixed models cost nothing on OpenRouter
        models={
            "fast": "meta-llama/llama-3.3-70b-instruct:free",
            "smart": "nvidia/nemotron-3-super-120b-a12b:free",  # deepseek-r1:free delisted 2026-06
        },
        rpm=20,
        rpd=50,  # 1000/day if the account has ever topped up $10
        priority=50,  # widest model catalog, but tightest free request cap
        referer="https://github.com/cheahjs/free-llm-api-resources",
    ),
]


def available_providers() -> List[Provider]:
    """Registry entries that actually have an API key set in the environment."""
    return [p for p in REGISTRY if p.api_key]
