"""Rate Limiter — control de consumo de llamadas LLM.

Sliding window: limita el número de llamadas por minuto y por hora.
Configurable desde Kairos feature flags.
"""

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate limiter con sliding window para llamadas LLM."""

    def __init__(self, max_per_minute: int = 20, max_per_hour: int = 200):
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self._minute_window: deque[float] = deque()
        self._hour_window: deque[float] = deque()
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return a lock bound to the current running loop."""
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if self._lock is None or (loop is not None and self._lock_loop is not loop):
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def acquire(self) -> bool:
        """Intenta adquirir un slot. Retorna True si está permitido, False si debe esperar."""
        async with self._get_lock():
            now = time.time()
            # Clean old entries
            while self._minute_window and now - self._minute_window[0] > 60:
                self._minute_window.popleft()
            while self._hour_window and now - self._hour_window[0] > 3600:
                self._hour_window.popleft()

            if len(self._minute_window) >= self.max_per_minute:
                return False
            if len(self._hour_window) >= self.max_per_hour:
                return False

            self._minute_window.append(now)
            self._hour_window.append(now)
            return True

    async def wait_and_acquire(self, timeout: float = 30) -> bool:
        """Espera hasta que haya un slot disponible o se alcance el timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.acquire():
                return True
            await asyncio.sleep(1)
        return False

    async def remaining(self) -> int:
        """Número de slots disponibles en la ventana actual."""
        async with self._get_lock():
            now = time.time()
            while self._minute_window and now - self._minute_window[0] > 60:
                self._minute_window.popleft()
            return max(0, self.max_per_minute - len(self._minute_window))

    @property
    def current_minute_count(self) -> int:
        return len(self._minute_window)

    @property
    def current_hour_count(self) -> int:
        return len(self._hour_window)


# Global instance — configurable via feature flags
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        from core.config import settings

        max_min = settings.llm_rate_per_minute
        max_hour = settings.llm_rate_per_hour
        _rate_limiter = RateLimiter(max_per_minute=max_min, max_per_hour=max_hour)
        logger.info(f"Rate limiter inicializado: {max_min}/min, {max_hour}/h")
    return _rate_limiter
