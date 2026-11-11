# core/provider_health.py — salud del proveedor LLM visible en la GUI
"""Última llamada, latencia y fallos consecutivos del proveedor online.

La ventana muda del proveedor degradado (26-ago: densidad de POSTs en
caída y pasos congelados) era invisible hasta que el paso moría. Este
registro alimenta la línea de salud del monitor de Sistema.
"""

from __future__ import annotations

import threading
import time

__all__ = ["record_success", "record_failure", "snapshot"]

_lock = threading.Lock()
_state: dict = {
    "provider": "",
    "last_latency_s": None,
    "last_success_at": None,
    "consecutive_failures": 0,
    "last_error": "",
}


def record_success(provider: str, latency_s: float) -> None:
    with _lock:
        _state["provider"] = str(provider or "")
        _state["last_latency_s"] = float(latency_s)
        _state["last_success_at"] = time.time()
        _state["consecutive_failures"] = 0
        _state["last_error"] = ""


def record_failure(provider: str, error: str) -> None:
    with _lock:
        _state["provider"] = str(provider or _state["provider"])
        _state["consecutive_failures"] += 1
        _state["last_error"] = str(error)[:200]


def snapshot() -> dict:
    """Copia del estado (segura para la GUI)."""
    with _lock:
        return dict(_state)
