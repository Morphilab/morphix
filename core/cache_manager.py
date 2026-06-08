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

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    total_prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    total_completion_tokens: int = 0
    llm_calls: int = 0
    last_updated: float = 0.0

    @property
    def hit_rate(self) -> float:
        total = self.cache_hit_tokens + self.cache_miss_tokens
        if total == 0:
            return 0.0
        return self.cache_hit_tokens / total

    @property
    def tokens_saved(self) -> int:
        return self.cache_hit_tokens


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

    def _init(self) -> None:
        self._global_stats = CacheStats()
        self._workspace_stats: dict[str, CacheStats] = {}
        logger.info("Prompt Cache Manager initialized (DeepSeek auto-cache)")

    def track_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        prompt_cache_hit_tokens: int = 0,
        prompt_cache_miss_tokens: int = 0,
        workspace: str = "main",
    ) -> None:
        """Record token usage and cache metrics from an LLM response."""
        with self._lock:
            # Global stats
            self._global_stats.total_prompt_tokens += prompt_tokens
            self._global_stats.cache_hit_tokens += prompt_cache_hit_tokens
            self._global_stats.cache_miss_tokens += prompt_cache_miss_tokens
            self._global_stats.total_completion_tokens += completion_tokens
            self._global_stats.llm_calls += 1
            self._global_stats.last_updated = time.time()

            # Per-workspace stats
            ws = self._workspace_stats.setdefault(workspace, CacheStats())
            ws.total_prompt_tokens += prompt_tokens
            ws.cache_hit_tokens += prompt_cache_hit_tokens
            ws.cache_miss_tokens += prompt_cache_miss_tokens
            ws.total_completion_tokens += completion_tokens
            ws.llm_calls += 1
            ws.last_updated = time.time()

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
