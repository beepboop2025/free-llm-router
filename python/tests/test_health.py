"""CircuitBreaker state machine: closed -> open -> half_open -> closed/open."""

from __future__ import annotations

import pytest

from free_llm_router.health import CircuitBreaker, State


@pytest.mark.asyncio
async def test_starts_closed_and_allows(clock):
    cb = CircuitBreaker(monotonic=clock)
    assert cb.state is State.CLOSED
    assert await cb.allow() is True


@pytest.mark.asyncio
async def test_opens_after_threshold_consecutive_failures(clock):
    cb = CircuitBreaker(monotonic=clock, failure_threshold=3)
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state is State.CLOSED  # 2 < 3, still closed
    await cb.record_failure()
    assert cb.state is State.OPEN
    # Open circuit rejects fast (no time advanced past cooldown).
    assert await cb.allow() is False


@pytest.mark.asyncio
async def test_success_resets_failure_count(clock):
    cb = CircuitBreaker(monotonic=clock, failure_threshold=3)
    await cb.record_failure()
    await cb.record_failure()
    await cb.record_success()          # resets the counter to 0
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state is State.CLOSED    # only 2 failures since the reset


@pytest.mark.asyncio
async def test_transitions_to_half_open_after_cooldown(clock):
    cb = CircuitBreaker(monotonic=clock, failure_threshold=1, cooldown_sec=30.0)
    await cb.record_failure()          # opens immediately
    assert cb.state is State.OPEN
    assert await cb.allow() is False   # still cooling down
    clock.advance(30.0)
    # cooldown elapsed → allow() promotes to half_open and lets ONE probe through
    assert await cb.allow() is True
    assert cb.state is State.HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_allows_exactly_one_probe(clock):
    cb = CircuitBreaker(monotonic=clock, failure_threshold=1, cooldown_sec=10.0)
    await cb.record_failure()
    clock.advance(10.0)
    assert await cb.allow() is True    # the single probe
    # Every subsequent allow() while the probe is in flight must be refused —
    # this is the oscillation bug the breaker exists to prevent.
    assert await cb.allow() is False
    assert await cb.allow() is False


@pytest.mark.asyncio
async def test_half_open_success_closes_circuit(clock):
    cb = CircuitBreaker(monotonic=clock, failure_threshold=1, cooldown_sec=10.0)
    await cb.record_failure()
    clock.advance(10.0)
    await cb.allow()                   # take the probe
    await cb.record_success()
    assert cb.state is State.CLOSED
    assert await cb.allow() is True    # fully back in rotation


@pytest.mark.asyncio
async def test_half_open_failure_reopens_and_restarts_cooldown(clock):
    cb = CircuitBreaker(monotonic=clock, failure_threshold=1, cooldown_sec=10.0)
    await cb.record_failure()
    clock.advance(10.0)
    await cb.allow()                   # probe
    await cb.record_failure()          # probe failed
    assert cb.state is State.OPEN
    # Cooldown restarts from the moment of the failed probe: still closed for 9s.
    clock.advance(9.0)
    assert await cb.allow() is False
    clock.advance(1.0)
    assert await cb.allow() is True    # 10s after the failed probe → probe again
