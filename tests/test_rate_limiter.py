"""Tests for Rate Limiter — sliding window, acquire, wait_and_acquire."""

import pytest

from core.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_within_limits(self):
        rl = RateLimiter(max_per_minute=5, max_per_hour=10)
        for _ in range(5):
            assert await rl.acquire() is True

    @pytest.mark.asyncio
    async def test_acquire_hits_minute_cap(self):
        rl = RateLimiter(max_per_minute=2, max_per_hour=100)
        assert await rl.acquire() is True
        assert await rl.acquire() is True
        assert await rl.acquire() is False

    @pytest.mark.asyncio
    async def test_acquire_hits_hour_cap(self):
        rl = RateLimiter(max_per_minute=100, max_per_hour=2)
        assert await rl.acquire() is True
        assert await rl.acquire() is True
        assert await rl.acquire() is False

    @pytest.mark.asyncio
    async def test_wait_and_acquire_succeeds(self):
        rl = RateLimiter(max_per_minute=5, max_per_hour=100)
        assert await rl.wait_and_acquire(timeout=5) is True

    @pytest.mark.asyncio
    async def test_wait_and_acquire_times_out(self):
        rl = RateLimiter(max_per_minute=1, max_per_hour=100)
        import time

        rl._minute_window.append(time.time())  # fresh timestamp blocks acquire
        assert await rl.acquire() is False
        assert await rl.wait_and_acquire(timeout=0.1) is False

    @pytest.mark.asyncio
    async def test_current_counts(self):
        rl = RateLimiter(max_per_minute=5, max_per_hour=10)
        await rl.acquire()
        await rl.acquire()
        assert rl.current_minute_count == 2
        assert rl.current_hour_count == 2

    @pytest.mark.asyncio
    async def test_remaining_slots(self):
        rl = RateLimiter(max_per_minute=5, max_per_hour=100)
        remaining = await rl.remaining()
        assert remaining == 5
        await rl.acquire()
        await rl.acquire()
        remaining = await rl.remaining()
        assert remaining == 3

    @pytest.mark.asyncio
    async def test_remaining_zero_when_full(self):
        rl = RateLimiter(max_per_minute=2, max_per_hour=100)
        await rl.acquire()
        await rl.acquire()
        remaining = await rl.remaining()
        assert remaining == 0
