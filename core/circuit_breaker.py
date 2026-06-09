"""Circuit Breaker — protege contra fallos en cascada en llamadas externas.

Implementa el patrón Circuit Breaker para proveedores LLM:
- CLOSED: operación normal, se envían requests
- OPEN: demasiados fallos consecutivos, se rechazan requests inmediatamente
- HALF_OPEN: timeout de recuperación expirado, se permite un request de prueba
"""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    """Circuit breaker para un proveedor externo.

    Args:
        failure_threshold: fallos consecutivos para abrir el circuito
        recovery_timeout: segundos antes de intentar half-open
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _state: str = field(default="closed", init=False)
    _failures: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)

    def allow_request(self) -> bool:
        """True si el request debe enviarse, False si debe rechazarse (circuito abierto)."""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = "half_open"
                    return True
                return False
            # half_open: allow one probe request
            return True

    def record_success(self) -> None:
        """Cierra el circuito tras un request exitoso."""
        with self._lock:
            self._state = "closed"
            self._failures = 0

    def record_failure(self) -> None:
        """Registra un fallo. Si se supera el umbral, abre el circuito."""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._failures >= self.failure_threshold:
                self._state = "open"

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._state == "open"

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures


class CircuitBreakerRegistry:
    """Registro global de circuit breakers por proveedor."""

    _breakers: dict[str, CircuitBreaker] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls, provider: str) -> CircuitBreaker:
        with cls._lock:
            if provider not in cls._breakers:
                cls._breakers[provider] = CircuitBreaker()
            return cls._breakers[provider]

    @classmethod
    def reset_all(cls) -> None:
        with cls._lock:
            cls._breakers.clear()

    @classmethod
    def get_all_states(cls) -> dict[str, dict]:
        with cls._lock:
            return {
                name: {"state": cb.state, "failures": cb.failure_count}
                for name, cb in cls._breakers.items()
            }
