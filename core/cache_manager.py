# core/cache_manager.py
"""Prompt Cache Manager — multi-provider cache abstraction.

DeepSeek (now):  Automatic server-side disk caching. No client API needed.
                 We monitor cache hit/miss via response.usage fields.

Anthropic (future): Client-controlled ephemeral caching via cache_control markers.
                    We inject {"type": "ephemeral"} on system/tools messages.

OpenAI (future):   Automatic prompt caching (newer models). Monitor like DeepSeek.

Design:
    - CacheManager is a singleton that accumulates per-workspace cache stats.
    - track_usage() extracts prompt_cache_hit_tokens / prompt_cache_miss_tokens
      from any provider response.
    - get_stats() returns hit rate and token savings for reporting.
    - stabilize_messages() is a helper that keeps the message prefix intact
      (critical for DeepSeek's prefix-based disk caching).
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from core.path_resolver import paths

logger = logging.getLogger(__name__)

# Segundos mínimos entre escrituras a disco de las estadísticas (throttle).
_FLUSH_INTERVAL_SECONDS = 5.0


@dataclass
class CacheStats:
    total_prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    total_completion_tokens: int = 0
    llm_calls: int = 0
    last_updated: float = 0.0
    # Disjoint token buckets (token-meter). `total_prompt_tokens` keeps the wire
    # value (includes cache hits); `uncached_input_tokens` is the disjoint input.
    uncached_input_tokens: int = 0
    cache_write_tokens: int = 0
    # Epoch del call-config (provider|model|max_tokens): cambios detectados indican
    # invalidación del prefijo → menos prompt-cache hasta reconstruirlo.
    last_epoch: str = ""
    epoch_changes: int = 0
    # Ventana de configs recientes: alternar agent↔fast es el patrón
    # NORMAL de un turno de bot (finalizer) — cada alternancia invalidaba el cache
    # con un WARNING de ruido. Una config ya vista en la ventana se loguea a DEBUG.
    recent_epochs: tuple[str, ...] = ()

    @property
    def hit_rate(self) -> float:
        total = self.cache_hit_tokens + self.cache_miss_tokens
        if total == 0:
            return 0.0
        return self.cache_hit_tokens / total

    @property
    def tokens_saved(self) -> int:
        return self.cache_hit_tokens

    @property
    def billed_input_tokens(self) -> int:
        return self.uncached_input_tokens + self.cache_hit_tokens + self.cache_write_tokens

    @property
    def billed_total_tokens(self) -> int:
        return self.billed_input_tokens + self.total_completion_tokens


class CacheManager:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self, stats_path: str | Path | None = None) -> None:
        self._global_stats = CacheStats()
        self._workspace_stats: dict[str, CacheStats] = {}
        self._stats_path = (
            Path(stats_path) if stats_path else (paths.memory_base() / "llm_cache_stats.json")
        )
        self._last_flush = time.time()
        self._load_from_disk()
        logger.info("Prompt Cache Manager initialized (DeepSeek auto-cache)")

    def _stats_payload(self) -> dict[str, Any]:
        """Serializa global + workspaces a un dict plano."""
        return {
            "version": 1,
            "global": asdict(self._global_stats),
            "workspaces": {ws: asdict(st) for ws, st in self._workspace_stats.items()},
        }

    def _load_from_disk(self) -> None:
        """Restaura estadísticas desde disco (tolerante a archivo corrupto)."""
        try:
            if not self._stats_path.is_file():
                return
            with open(self._stats_path, encoding="utf-8") as f:
                payload = json.load(f)
            valid_fields = {f.name for f in fields(CacheStats)}
            for section, target in ((payload.get("global"), self._global_stats),):
                if isinstance(section, dict):
                    for k, v in section.items():
                        if k in valid_fields:
                            setattr(target, k, v)
            for ws, data in (payload.get("workspaces") or {}).items():
                if not isinstance(data, dict):
                    continue
                st = self._workspace_stats.setdefault(ws, CacheStats())
                for k, v in data.items():
                    if k in valid_fields:
                        setattr(st, k, v)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("llm_cache_stats ilegible (%s): %s", self._stats_path, e)

    def flush_to_disk(self) -> None:
        """Escritura atómica (temp + os.replace) de las estadísticas."""
        with self._lock:
            path = self._stats_path
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._stats_payload(), f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                self._last_flush = time.time()
            except OSError as e:  # pragma: no cover — disco lleno/permisos, fail-soft
                logger.warning("No se pudo persistir llm_cache_stats: %s", e)

    def _maybe_flush(self) -> None:
        now = time.time()
        if now - self._last_flush >= _FLUSH_INTERVAL_SECONDS:
            self.flush_to_disk()

    def note_epoch(
        self,
        workspace: str,
        provider: str,
        model: str | None,
        max_tokens: int | None,
    ) -> str:
        """Registra el epoch del call-config y detecta cambios (invalidación).

        Sigue la convención conservadora: model + provider +
        max_tokens son epoch-level; un cambio implica que el prefijo cacheable se
        reconstruye → se cuenta como invalidación.
        """
        raw = "|".join([provider or "", str(model or ""), str(max_tokens or "")])
        epoch = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        with self._lock:
            stats = self._workspace_stats.setdefault(workspace, CacheStats())
            prev = stats.last_epoch or self._global_stats.last_epoch
            changed = bool(prev and prev != epoch)
            stats.last_epoch = epoch
            self._global_stats.last_epoch = epoch
            if changed:
                stats.epoch_changes += 1
                self._global_stats.epoch_changes += 1
                # alternar agent↔fast (patrón normal de un turno de
                # bot) producía un WARNING por cada alternancia. La config ya
                # vista en la ventana reciente es ruido conocido → DEBUG; solo
                # una config GENUINAMENTE nueva advierte.
                window = stats.recent_epochs + self._global_stats.recent_epochs
                if epoch in window:
                    logger.debug(
                        "Caché prompt-posible: call-config alternante (%s): %s → %s",
                        workspace,
                        prev,
                        epoch,
                    )
                else:
                    logger.warning(
                        "Caché prompt-posible invalidada por cambio de call-config "
                        "(workspace=%s): %s → %s",
                        workspace,
                        prev,
                        epoch,
                    )
                stats.recent_epochs = (*stats.recent_epochs, prev, epoch)[-4:]
                self._global_stats.recent_epochs = (
                    *self._global_stats.recent_epochs,
                    prev,
                    epoch,
                )[-4:]
            return epoch

    @staticmethod
    def cache_friendly_compress(messages: list[dict], max_tokens: int) -> list[dict]:
        """Compresión de contexto consciente del prompt-cache.

        Con ``PROMPT_CACHE_PRESERVE_PREFIX`` activo (default), preserva el prefijo
        cacheable vía stabilize_messages; si está desactivado, cae al
        compress_history clásico (comportamiento pre-absorción).
        """
        from core.config import settings

        if getattr(settings, "prompt_cache_preserve_prefix", True):
            return CacheManager.stabilize_messages(messages, max_tokens)
        from core.context_manager import ContextManager

        return ContextManager.compress_history(messages, max_tokens)

    def track_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        prompt_cache_hit_tokens: int = 0,
        prompt_cache_miss_tokens: int = 0,
        workspace: str = "main",
        uncached_input_tokens: int | None = None,
        cache_write_tokens: int | None = None,
    ) -> None:
        """Record token usage and cache metrics from an LLM response.

        ``prompt_tokens`` keeps the wire value (includes cache hits). When the
        caller has the disjoint decomposition, pass ``uncached_input_tokens`` and
        ``cache_write_tokens`` to populate the token-meter buckets.
        """
        with self._lock:
            # Global stats
            self._global_stats.total_prompt_tokens += prompt_tokens
            self._global_stats.cache_hit_tokens += prompt_cache_hit_tokens
            self._global_stats.cache_miss_tokens += prompt_cache_miss_tokens
            self._global_stats.total_completion_tokens += completion_tokens
            self._global_stats.llm_calls += 1
            self._global_stats.last_updated = time.time()
            if uncached_input_tokens is not None:
                self._global_stats.uncached_input_tokens += uncached_input_tokens
            if cache_write_tokens is not None:
                self._global_stats.cache_write_tokens += cache_write_tokens

            # Per-workspace stats
            ws = self._workspace_stats.setdefault(workspace, CacheStats())
            ws.total_prompt_tokens += prompt_tokens
            ws.cache_hit_tokens += prompt_cache_hit_tokens
            ws.cache_miss_tokens += prompt_cache_miss_tokens
            ws.total_completion_tokens += completion_tokens
            ws.llm_calls += 1
            ws.last_updated = time.time()
            if uncached_input_tokens is not None:
                ws.uncached_input_tokens += uncached_input_tokens
            if cache_write_tokens is not None:
                ws.cache_write_tokens += cache_write_tokens

            self._maybe_flush()

            if prompt_cache_hit_tokens > 0 or prompt_cache_miss_tokens > 0:
                hit_rate = (
                    prompt_cache_hit_tokens
                    / (prompt_cache_hit_tokens + prompt_cache_miss_tokens)
                    * 100
                )
                logger.debug(
                    f"DeepSeek cache: {prompt_cache_hit_tokens} hit / "
                    f"{prompt_cache_miss_tokens} miss "
                    f"({hit_rate:.0f}% hit rate)"
                )

    def get_stats(self, workspace: str | None = None) -> dict[str, Any]:
        """Return cache statistics as a dict for reporting."""
        with self._lock:
            stats = (
                self._workspace_stats.get(workspace, CacheStats())
                if workspace
                else self._global_stats
            )
            return {
                "prompt_tokens_total": stats.total_prompt_tokens,
                "completion_tokens_total": stats.total_completion_tokens,
                "cache_hit_tokens": stats.cache_hit_tokens,
                "cache_miss_tokens": stats.cache_miss_tokens,
                "cache_hit_rate": round(stats.hit_rate * 100, 1),
                "tokens_saved": stats.tokens_saved,
                "llm_calls": stats.llm_calls,
                "last_updated": stats.last_updated,
                "uncached_input_tokens": stats.uncached_input_tokens,
                "cache_write_tokens": stats.cache_write_tokens,
                "billed_input_tokens": stats.billed_input_tokens,
                "billed_total_tokens": stats.billed_total_tokens,
                "last_epoch": stats.last_epoch,
                "epoch_changes": stats.epoch_changes,
            }

    @staticmethod
    def stabilize_messages(messages: list[dict], max_tokens: int) -> list[dict]:
        """Compress messages while preserving the prefix for optimal caching.

        Unlike compress_history() which removes middle messages (breaking the
        prefix for DeepSeek's disk cache), this method keeps the beginning intact
        and summarizes the middle into a single injected context message.

        Strategy for DeepSeek::

            ``[system] [user1] [assistant1] [user2] [assistant2] ... [userN]``
            ``└────── PREFIX (cacheable) ──────┘└── middle ──┘└ recent ─┘``

        We keep: system + first 2 turns (prefix) + last 4 turns (recent)
        We summarize middle turns into a single system-injected context note.
        """
        from core.context_manager import ContextManager

        if not messages or len(messages) <= 6:
            return ContextManager.compress_history(messages, max_tokens)

        system = messages[0] if messages[0].get("role") == "system" else None
        offset = 1 if system else 0

        if len(messages) <= offset + 8:
            return ContextManager.compress_history(messages, max_tokens)

        prefix_count = min(3, len(messages) - offset - 4)
        recent_count = min(6, len(messages) - offset - prefix_count)

        prefix = messages[offset : offset + prefix_count]
        recent = messages[-(recent_count):]
        middle = (
            messages[offset + prefix_count : -recent_count]
            if recent_count
            else messages[offset + prefix_count :]
        )

        # Summarize middle
        summary_text = ContextManager.build_context_summary(
            list(middle), max_tokens=min(500, max_tokens // 4)
        )

        result = [system] if system else []
        result.extend(prefix)

        if summary_text:
            result.append(
                {
                    "role": "system",
                    "content": f"[Earlier context summary]\n{summary_text}",
                }
            )

        result.extend(recent)

        # Safety: if still too large, fall back to standard compression
        est = ContextManager.estimate_tokens(result)
        if est > max_tokens:
            return ContextManager.compress_history(messages, max_tokens)

        return result


cache_manager = CacheManager()
