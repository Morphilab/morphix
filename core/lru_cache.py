"""LRU Cache — thread-safe, TTL, tamaño limitado.

Usado por TaskAnalyzer y AgentRouter para cachear resultados de LLM.
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


class LRUCache:
    def __init__(self, max_size: int = 500, ttl: int = 300):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        """Retorna valor cacheado si existe y no expiró. None si no."""
        with self._lock:
            if key not in self._cache:
                return None
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self._ttl:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        """Guarda valor en cache con timestamp actual."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.time())
            # Evict LRU if exceeds size
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear_expired(self) -> int:
        """Elimina entradas expiradas. Retorna cuántas eliminó."""
        with self._lock:
            now = time.time()
            expired = [k for k, (_, ts) in list(self._cache.items()) if now - ts > self._ttl]
            for k in expired:
                self._cache.pop(k, None)
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
