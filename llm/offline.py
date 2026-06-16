# llm/offline.py
import asyncio
import logging
import time

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class OfflineManager:
    _is_offline: bool | None = None
    _last_check: float = 0
    _check_interval = 300  # 5 minutos

    async def detect(self) -> bool:
        """Detecta si realmente hay conexión a internet. Async, no bloquea el event loop."""
        endpoints = ["https://www.google.com", "https://x.ai"]
        async with httpx.AsyncClient(timeout=3.0) as client:
            for _attempt in range(3):
                try:
                    for endpoint in endpoints:
                        r = await client.get(endpoint)
                        if r.status_code == 200:
                            self._is_offline = False
                            self._last_check = time.time()
                            logger.info(f"🔍 OfflineManager: Conexión OK (endpoint: {endpoint})")
                            return False
                except Exception as e:
                    logger.debug(f"Offline check falló para {endpoint}: {e}")
                    await asyncio.sleep(1)
        self._is_offline = True
        self._last_check = time.time()
        logger.info("🔍 OfflineManager: Sin conexión detectada")
        return True

    def is_offline(self) -> bool:
        """Estado real: forzado por usuario O sin conexión (usa cache, sin I/O)."""
        return settings.offline_mode or (self._is_offline is True)

    def toggle_offline(self) -> bool:
        """Método centralizado y robusto para activar/desactivar modo offline."""
        new_state = not settings.offline_mode
        settings.offline_mode = new_state
        self._is_offline = new_state
        logger.info(f"🔌 Modo Offline {'activado' if new_state else 'desactivado'} por el usuario")
        return new_state
