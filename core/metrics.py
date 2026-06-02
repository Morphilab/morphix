"""Metrics — contadores de uso del sistema.

Métricas acumulativas: tokens, workflows, herramientas.
Métricas por herramienta: éxito/fallo, latencia.
Expuestas via comando :stats en CLI y panel desktop.
"""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Metrics:
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    total_tokens: int = 0
    total_workflows: int = 0
    completed_workflows: int = 0
    failed_workflows: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    rate_limited: int = 0
    start_time: float = field(default_factory=time.time)
    # Cache metrics (DeepSeek prompt caching)
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

    def record_workflow_completed(self, tokens: int = 0, tool_calls: int = 0) -> None:
        with self._lock:
            self.total_workflows += 1
            self.completed_workflows += 1
            self.total_tokens += tokens
            self.tool_calls += tool_calls

    def record_workflow_failed(self) -> None:
        with self._lock:
            self.total_workflows += 1
            self.failed_workflows += 1

    def record_llm_call(self) -> None:
        with self._lock:
            self.llm_calls += 1

    def record_llm_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
    ) -> None:
        """Record per-call token usage including cache hit/miss from DeepSeek."""
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_tokens += prompt_tokens + completion_tokens
            self.cache_hit_tokens += cache_hit_tokens
            self.cache_miss_tokens += cache_miss_tokens

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def to_dict(self) -> dict:
        with self._lock:
            uptime = int(time.time() - self.start_time)
            total_cache = self.cache_hit_tokens + self.cache_miss_tokens
            cache_hit_rate = (
                round(self.cache_hit_tokens / total_cache * 100, 1) if total_cache > 0 else 0.0
            )
            return {
                "uptime_seconds": uptime,
                "total_tokens": self.total_tokens,
                "total_workflows": self.total_workflows,
                "completed_workflows": self.completed_workflows,
                "failed_workflows": self.failed_workflows,
                "success_rate": f"{self.completed_workflows / max(self.total_workflows, 1) * 100:.1f}%",
                "tool_calls": self.tool_calls,
                "llm_calls": self.llm_calls,
                "rate_limited": self.rate_limited,
                "cache_hit_tokens": self.cache_hit_tokens,
                "cache_miss_tokens": self.cache_miss_tokens,
                "cache_hit_rate_pct": cache_hit_rate,
                "tokens_saved": self.cache_hit_tokens,
            }


# Instancia global
metrics = Metrics()


@dataclass
class ToolMetrics:
    """Métricas por herramienta: éxito/fallo y latencia."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _calls: dict[str, dict] = field(default_factory=dict)

    def record_call(self, tool_name: str, success: bool, latency_ms: float) -> None:
        """Registra una llamada a herramienta con su resultado y latencia."""
        with self._lock:
            if tool_name not in self._calls:
                self._calls[tool_name] = {
                    "total": 0,
                    "success": 0,
                    "failure": 0,
                    "latency_ms_total": 0.0,
                    "latency_ms_max": 0.0,
                }
            entry = self._calls[tool_name]
            entry["total"] += 1
            entry["latency_ms_total"] += latency_ms
            entry["latency_ms_max"] = max(entry["latency_ms_max"], latency_ms)
            if success:
                entry["success"] += 1
            else:
                entry["failure"] += 1

    def get_tool_stats(self, tool_name: str) -> dict | None:
        """Devuelve las métricas de una herramienta o None."""
        with self._lock:
            entry = self._calls.get(tool_name)
            if entry is None:
                return None
            return self._format_entry(tool_name, entry)

    def get_all_stats(self) -> dict[str, dict]:
        """Devuelve métricas de todas las herramientas."""
        with self._lock:
            return {name: self._format_entry(name, e) for name, e in self._calls.items()}

    def get_summary(self) -> dict:
        """Resumen agregado de todas las herramientas."""
        with self._lock:
            total = sum(e["total"] for e in self._calls.values())
            success = sum(e["success"] for e in self._calls.values())
            failure = sum(e["failure"] for e in self._calls.values())
            return {
                "total_calls": total,
                "success": success,
                "failure": failure,
                "success_rate_pct": round(success / max(total, 1) * 100, 1),
                "tools_tracked": len(self._calls),
            }

    def to_dict(self) -> dict:
        """Métricas completas serializables."""
        return {
            "summary": self.get_summary(),
            "tools": self.get_all_stats(),
        }

    @staticmethod
    def _format_entry(name: str, entry: dict) -> dict:
        total = max(entry["total"], 1)
        return {
            "tool": name,
            "calls": entry["total"],
            "success": entry["success"],
            "failure": entry["failure"],
            "success_rate_pct": round(entry["success"] / total * 100, 1),
            "avg_latency_ms": round(entry["latency_ms_total"] / total, 1),
            "max_latency_ms": round(entry["latency_ms_max"], 1),
        }


# Global per-tool metrics instance
tool_metrics = ToolMetrics()
