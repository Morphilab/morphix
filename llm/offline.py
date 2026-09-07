# llm/offline.py
import asyncio
import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class OfflineManager:
    """Detector de conectividad con estado COMPARTIDO a nivel de clase.

    TODAS las instancias leen/escriben OfflineManager._state_offline: el daemon
    de bootstrap y el toggle GUI deben operar sobre el mismo estado que
    consulta el provider.
    """

    _state_offline: bool | None = None  # None = aún no detectado

    async def detect(self) -> bool:
        """Detecta si realmente hay conexión a internet. Async, no bloquea."""
        endpoints = ["https://www.google.com", "https://x.ai"]
        async with httpx.AsyncClient(timeout=3.0) as client:
            for _attempt in range(3):
                try:
                    for endpoint in endpoints:
                        r = await client.get(endpoint)
                        if r.status_code == 200:
                            OfflineManager._state_offline = False
                            logger.info(f"🔍 OfflineManager: Conexión OK (endpoint: {endpoint})")
                            return False
                except Exception as e:
                    logger.debug(f"Offline check falló para {endpoint}: {e}")
                    await asyncio.sleep(1)
        OfflineManager._state_offline = True
        logger.info("🔍 OfflineManager: Sin conexión detectada")
        return True

    def is_offline(self) -> bool:
        """Estado real: forzado por usuario O sin conexión (usa cache, sin I/O)."""
        return settings.offline_mode or (OfflineManager._state_offline is True)

    def toggle_offline(self) -> bool:
        """Método centralizado y robusto para activar/desactivar modo offline."""
        new_state = not settings.offline_mode
        settings.offline_mode = new_state
        OfflineManager._state_offline = new_state
        logger.info(f"🔌 Modo Offline {'activado' if new_state else 'desactivado'} por el usuario")
        return new_state
