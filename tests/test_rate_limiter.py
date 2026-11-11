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


class TestReadTimeoutConfigurable:
    def test_read_timeout_explicito_y_derivado(self, monkeypatch):
        """LLM_READ_TIMEOUT gobierna el read-timeout; sin él, el derivado
        connect*3 (una conexión muerta colgaba el paso hasta 10 min)."""
        from core.config import settings
        from llm import provider as prov

        monkeypatch.setattr(settings, "llm_read_timeout", 300)
        c = prov._http_client(60)
        assert c.timeout.read == 300
        c.close()
        ca = prov._http_async_client(60)
        assert ca.timeout.read == 300

        monkeypatch.setattr(settings, "llm_read_timeout", 0)
        c2 = prov._http_client(60)
        assert c2.timeout.read == 180
        c2.close()


class TestProviderHealth:
    def test_recorder_ciclo_completo(self):
        from core import provider_health as ph

        ph.record_success("deepseek", 1.5)
        s = ph.snapshot()
        assert s["provider"] == "deepseek"
        assert s["last_latency_s"] == 1.5
        assert s["consecutive_failures"] == 0

        ph.record_failure("deepseek", "timeout")
        ph.record_failure("deepseek", "timeout")
        s2 = ph.snapshot()
        assert s2["consecutive_failures"] == 2
        assert "timeout" in s2["last_error"]

        ph.record_success("deepseek", 2.0)
        assert ph.snapshot()["consecutive_failures"] == 0


class TestMetricaZombiesVisible:
    def test_sandbox_zombie_sale_en_to_dict(self):
        from core.metrics import Metrics

        m = Metrics()
        m.record_sandbox_zombie()
        assert m.to_dict()["sandbox_zombie_threads"] == 1
