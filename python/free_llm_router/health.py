"""
Per-provider circuit breaker.

A free provider that starts 500-ing or timing out should be taken out of the
rotation quickly and probed cautiously — otherwise every request pays the full
timeout before failing over, and a flapping provider gets hammered.

States:
  closed     – normal; requests flow.
  open       – too many recent failures; reject fast until cooldown elapses.
  half_open  – cooldown elapsed; allow EXACTLY ONE probe. If it succeeds we
               close; if it fails we re-open. Letting many probes through at
               once was a real bug — they all rush the still-broken provider
               and the breaker oscillates.
"""

from __future__ import annotations

import asyncio
from enum import Enum


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        monotonic,
        failure_threshold: int = 3,
        cooldown_sec: float = 30.0,
    ) -> None:
        self._monotonic = monotonic
        self._failure_threshold = failure_threshold
        self._cooldown_sec = cooldown_sec
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    async def allow(self) -> bool:
        """Whether a request may proceed right now."""
        async with self._lock:
            if self._state is State.CLOSED:
                return True
            if self._state is State.OPEN:
                if self._monotonic() - self._opened_at >= self._cooldown_sec:
                    # cooldown elapsed → permit a single probe
                    self._state = State.HALF_OPEN
                    self._probe_in_flight = True
                    return True
                return False
            # HALF_OPEN: only the one in-flight probe is allowed
            if not self._probe_in_flight:
                self._probe_in_flight = True
                return True
            return False

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._probe_in_flight = False
            self._state = State.CLOSED

    async def record_failure(self) -> None:
        async with self._lock:
            self._probe_in_flight = False
            if self._state is State.HALF_OPEN:
                # probe failed → straight back to open, restart cooldown
                self._state = State.OPEN
                self._opened_at = self._monotonic()
                return
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._state = State.OPEN
                self._opened_at = self._monotonic()

    @property
    def state(self) -> State:
        return self._state
