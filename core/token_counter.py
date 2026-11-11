"""Token Counter — carga lazy de tiktoken.

Centraliza la codificación cl100k_base para evitar cargarla en 4 lugares distintos.
"""

import logging

logger = logging.getLogger(__name__)

_enc = None


def get_encoding():
    """Return the tiktoken cl100k_base encoding with lazy loading.

    La primera llamada carga el encoding (~1-2 MB, ~100ms).
    Llamadas subsecuentes retornan la instancia cacheada.
    """
    global _enc
    if _enc is None:
        try:
            import tiktoken

            _enc = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.warning("tiktoken no instalado. Conteo de tokens no disponible.")
            return None
        except Exception as e:
            logger.warning(f"Error cargando tiktoken: {e}")
            return None
    return _enc
