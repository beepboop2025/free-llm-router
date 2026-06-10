"""free-llm-router: failover across perpetually-free, OpenAI-compatible LLM APIs."""

from .health import CircuitBreaker, State
from .providers import Provider, REGISTRY, available_providers
from .ratelimit import TokenBucket
from .router import (
    AllProvidersFailed,
    FreeLLMRouter,
    OrderFn,
    ProviderStats,
    TASK_TIER,
    default_order,
)

__all__ = [
    "FreeLLMRouter",
    "Provider",
    "ProviderStats",
    "OrderFn",
    "default_order",
    "AllProvidersFailed",
    "TASK_TIER",
    "REGISTRY",
    "available_providers",
    "TokenBucket",
    "CircuitBreaker",
    "State",
]

__version__ = "0.1.0"
