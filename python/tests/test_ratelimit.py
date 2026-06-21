"""TokenBucket: RPM burst + continuous refill + daily counter."""

from __future__ import annotations

import pytest

from free_llm_router.ratelimit import TokenBucket


@pytest.mark.asyncio
async def test_full_bucket_allows_a_burst_equal_to_rpm(clock):
    bucket = TokenBucket(rpm=3, monotonic=clock)
    # Three immediate acquisitions succeed (full capacity), the fourth is denied
    # because no time has passed to refill.
    assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is False


@pytest.mark.asyncio
async def test_each_acquire_increments_day_count(clock):
    bucket = TokenBucket(rpm=5, monotonic=clock)
    await bucket.try_acquire()
    await bucket.try_acquire()
    assert bucket.day_count == 2
    # A denied acquire must NOT bump the daily counter.
    drained = TokenBucket(rpm=1, monotonic=clock)
    await drained.try_acquire()       # consumes the one token
    await drained.try_acquire()       # denied
    assert drained.day_count == 1


@pytest.mark.asyncio
async def test_refill_is_proportional_to_elapsed_time(clock):
    # rpm=60 → 1 token/sec. Drain it, then advance time and watch it refill.
    bucket = TokenBucket(rpm=60, monotonic=clock)
    # drain all 60 tokens
    for _ in range(60):
        assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is False
    # half a second → only 0.5 token, still not enough for a whole one
    clock.advance(0.5)
    assert await bucket.try_acquire() is False
    # another half second → now a full token is available
    clock.advance(0.5)
    assert await bucket.try_acquire() is True


@pytest.mark.asyncio
async def test_refill_never_exceeds_capacity(clock):
    bucket = TokenBucket(rpm=2, monotonic=clock)
    # Idle for a long time; tokens must cap at capacity (2), not accumulate to 100s.
    clock.advance(10_000)
    assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is False


@pytest.mark.asyncio
async def test_seconds_until_token_zero_when_available(clock):
    bucket = TokenBucket(rpm=60, monotonic=clock)
    assert await bucket.seconds_until_token() == 0.0


@pytest.mark.asyncio
async def test_seconds_until_token_estimates_wait_when_empty(clock):
    bucket = TokenBucket(rpm=60, monotonic=clock)  # 1 token/sec
    for _ in range(60):
        await bucket.try_acquire()
    # empty → need 1 full token at 1/sec → ~1 second
    wait = await bucket.seconds_until_token()
    assert wait == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_reset_day_clears_only_the_daily_counter(clock):
    bucket = TokenBucket(rpm=5, monotonic=clock)
    await bucket.try_acquire()
    await bucket.try_acquire()
    assert bucket.day_count == 2
    bucket.reset_day()
    assert bucket.day_count == 0
    # RPM tokens are independent of the day reset — still spendable.
    assert await bucket.try_acquire() is True


@pytest.mark.asyncio
async def test_rpm_floor_of_one_for_zero_or_negative(clock):
    # Capacity is clamped to >= 1 so a misconfigured rpm=0 still serves one token.
    bucket = TokenBucket(rpm=0, monotonic=clock)
    assert await bucket.try_acquire() is True
    assert await bucket.try_acquire() is False
