"""
Per-provider rate limiting.

Free tiers police two axes simultaneously: requests-per-minute (burst) and
requests-per-day (quota). We enforce both:

  * RPM via a classic token bucket (smooth refill, allows short bursts).
  * RPD via a simple daily counter the caller resets out-of-band.

Async-safe. A subtle TOCTOU bug bit an earlier project: refilling tokens only
in ``acquire`` let two coroutines both see "1 token left" before either consumed.
Here refill happens under the same lock that does the consume, so check-and-take
is atomic.
"""

from __future__ import annotations

import asyncio


class TokenBucket:
    """Async token bucket: ``rpm`` tokens, refilled continuously."""

    def __init__(self, rpm: int, *, monotonic) -> None:
        # `monotonic` is injected (time.monotonic) so tests can supply a fake clock.
        self._capacity = float(max(rpm, 1))
        self._tokens = float(max(rpm, 1))
        self._refill_per_sec = max(rpm, 1) / 60.0
        self._monotonic = monotonic
        self._last = monotonic()
        self._lock = asyncio.Lock()
        self._day_count = 0

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)
            self._last = now

    async def try_acquire(self) -> bool:
        """Take one token if available. Returns False instead of blocking."""
        async with self._lock:
            self._refill()  # refill INSIDE the lock — atomic with the consume below
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._day_count += 1
                return True
            return False

    async def seconds_until_token(self) -> float:
        """How long until at least one token is available (for backoff hints)."""
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                return 0.0
            return (1.0 - self._tokens) / self._refill_per_sec

    @property
    def day_count(self) -> int:
        return self._day_count

    def reset_day(self) -> None:
        self._day_count = 0
