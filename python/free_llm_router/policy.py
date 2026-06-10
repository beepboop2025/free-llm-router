"""
Provider ordering policy — YOUR decision point.

The router calls an ``OrderFn`` before every request to decide which provider to
try first, second, third… Given a live snapshot of each provider's state, return
the providers in the order you want them attempted.

The default policy (``free_llm_router.router.default_order``) sorts by static
``priority`` only. That's fine until reality intrudes:
  * The top-priority provider is rate-limited *this minute* — trying it first just
    wastes a failover hop (it'll be skipped, but it's still first in line).
  * A provider has burned 49/50 of its daily quota — maybe save it for last.
  * One provider has been consistently slow (high ``last_latency_ms``).
  * A provider's circuit is half_open — risky; maybe deprioritize.

`ProviderStats` gives you, per provider:
    .provider.priority      static rank (lower = preferred)
    .circuit_state          "closed" | "open" | "half_open"
    .tokens_available       bool — has an RPM token to spend right now
    .day_count / .day_limit requests spent today / documented daily cap (cap may be None)
    .last_latency_ms        most recent successful round-trip, 0.0 if never called

Tradeoffs to weigh:
  - Latency-first ordering gets fast answers but can stampede one provider until
    it rate-limits, then thrash.
  - Quota-preserving ordering (spread load, save scarce daily quotas for last)
    is gentler on the free tiers — which is the whole point of not getting banned.
  - Health-first ordering avoids dead providers but a pure "closed-circuits-first"
    sort ignores speed and quota entirely.

There is no single right answer — it depends on whether you optimize for speed,
for staying under the free caps, or for resilience. That's why it's yours.
"""

from __future__ import annotations

from typing import List

from .router import ProviderStats, default_order
from .providers import Provider


def smart_order(stats: List[ProviderStats]) -> List[Provider]:
    """
    TODO(you): Rank providers for the next request.

    Return a list[Provider] in the order they should be tried. You don't have to
    include every provider, but anything you drop simply won't be attempted this
    call (the router still skips rate-limited / open-circuit ones defensively, so
    dropping them is optional).

    Suggested shape — sort by a tuple of keys, cheapest-to-violate first, e.g.:

        def rank(s: ProviderStats):
            return (
                0 if s.circuit_state == "closed" else 1,   # healthy first
                0 if s.tokens_available else 1,             # ready-now first
                ???,                                        # your quota / latency call
                s.provider.priority,                        # static tie-break
            )
        return [s.provider for s in sorted(stats, key=rank)]

    Replace the line below with your implementation.
    """
    return default_order(stats)  # placeholder — delegates to static priority
