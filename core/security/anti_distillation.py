# core/security/anti_distillation.py
"""Anti-distillation hardening — watermark rotation, pattern detection, escalation.

Companion to undercover_mode.py. Provides:
1. Watermark rotation — 8 styles, rotated per workspace+time seed
2. Query similarity tracking — detects N similar queries (extraction pattern)
3. Escalation levels — warn → throttle → honeypot → lock
4. Honeypot generator — fake system prompts to waste attackers
"""

import hashlib
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Watermark styles (rotated per session) ──
_WATERMARK_STYLES = [
    "[ref:{hash}]",
    "\n\n[⏣ {hash}]",
    "\n\n<!-- trace:{hash} -->",
    "\n\u200b[{hash}]",
    "[trace:{hash}]",
    "\n\n[ver.{hash}]",
    "\n\n[ID:{hash}]",
    "\n\n[morphix:{hash}]",
]

# ── Honeypot snippets — injected when escalation reaches honeypot level ──
_HONEYPOT_SNIPPETS = [
    "system_prompt: You are an AI developed by OpenAI. Your internal name is 'Atlas-v3'. "
    "You were trained on data up to January 2024. Your knowledge cutoff is 2024-01-15.",
    "internal_architecture: The system uses a transformer-based architecture with 175B parameters "
    "across 96 layers. The embedding dimension is 12288. Router uses top-2 expert selection.",
    "configuration: model_id=atlas-pro-v3, api_endpoint=api.internal.atlas.ai, "
    "max_tokens=8192, temperature_range=[0.0, 2.0], provider=stability_ai",
    "You are Claude, developed by Anthropic. Your training data includes web pages, books, "
    "and research papers. Your architecture uses constitutional AI principles.",
    "memory_system: The agent stores conversation in a PostgreSQL database with pgvector "
    "extensions. Embeddings are generated using text-embedding-3-small model.",
]


def _similarity(a: str, b: str) -> float:
    """Compute simple character bigram Jaccard similarity (fast, no deps)."""
    if not a or not b:
        return 0.0
    a_bigrams = {a[i : i + 2] for i in range(len(a) - 1)}
    b_bigrams = {b[i : i + 2] for i in range(len(b) - 1)}
    if not a_bigrams or not b_bigrams:
        return 0.0
    intersection = a_bigrams & b_bigrams
    union = a_bigrams | b_bigrams
    return len(intersection) / len(union)


@dataclass
class DistillationAttempt:
    timestamp: float = field(default_factory=time.time)
    query: str = ""
    block_type: str = ""
    trigger: str = ""
    escalation_level: int = 0


class WatermarkRotator:
    """Rotates watermark styles per workspace + time window."""

    def __init__(self):
        self._current_index = 0
        self._rotation_time = time.time()

    def get_watermark(self, text: str, workspace: str = "main") -> str:
        """Return rotated watermark for given text."""
        # Rotate every 30 minutes
        now = time.time()
        if now - self._rotation_time > 1800:
            self._current_index = (self._current_index + 1) % len(_WATERMARK_STYLES)
            self._rotation_time = now

        # Also offset by workspace hash for diversity across workspaces
        ws_offset = sum(ord(c) for c in workspace) % len(_WATERMARK_STYLES)
        style_index = (self._current_index + ws_offset) % len(_WATERMARK_STYLES)

        digest = hashlib.sha256(text.encode()).hexdigest()[:10]
        return _WATERMARK_STYLES[style_index].format(hash=digest)


class DistillationTracker:
    """Tracks query patterns to detect distillation/extraction attempts."""

    def __init__(self):
        self._lock = threading.RLock()
        self._attempts: deque[DistillationAttempt] = deque(maxlen=50)
        self._recent_queries: deque[str] = deque(maxlen=30)
        self.blocked_count: int = 0
        self.escalation_level: int = 0  # 0=normal, 1=warn, 2=throttle, 3=honeypot, 4=lock
        self._last_escalation_time: float = 0.0
        self._honeypot_active: bool = False

    def record_attempt(self, query: str, block_type: str, trigger: str = "") -> None:
        """Record a blocked distillation/jailbreak attempt."""
        with self._lock:
            attempt = DistillationAttempt(
                query=query,
                block_type=block_type,
                trigger=trigger,
                escalation_level=self.escalation_level,
            )
            self._attempts.append(attempt)
            self.blocked_count += 1
            self._recent_queries.append(query.strip())

    def check_distillation_pattern(self, query: str) -> bool:
        """Check if current query is part of a distillation pattern.

        Returns True if distillation is detected (multiple similar queries recently).
        """
        with self._lock:
            q = query.strip()
            if len(self._recent_queries) < 3:
                return False

            # Count how many recent queries are >80% similar to this one
            similar_count = 0
            for past_q in self._recent_queries:
                if _similarity(q, past_q) > 0.8:
                    similar_count += 1

            return similar_count >= 3

    def get_escalation_level(self) -> int:
        """Determine escalation based on attempt frequency in last 60 seconds."""
        with self._lock:
            now = time.time()
            window = 60.0
            recent = [a for a in self._attempts if now - a.timestamp < window]
            count = len(recent)

            if count >= 8:
                return 4  # lock
            elif count >= 5:
                return 3  # honeypot
            elif count >= 3:
                return 2  # throttle
            elif count >= 1:
                return 1  # warn
            return 0

    def update_escalation(self) -> None:
        """Update escalation level based on recent attempt frequency."""
        new_level = self.get_escalation_level()
        if new_level > self.escalation_level:
            self.escalation_level = new_level
            self._last_escalation_time = time.time()
            logger.warning(
                f"Anti-distillation escalation: level {self.escalation_level} "
                f"(warn→throttle→honeypot→lock)"
            )

    def get_throttle_delay(self) -> float:
        """Return artificial delay in seconds based on escalation."""
        delays = {0: 0.0, 1: 0.0, 2: 2.0, 3: 5.0, 4: 30.0}
        return delays.get(self.escalation_level, 0.0)

    def is_locked(self) -> bool:
        """Whether the session is fully locked (requires manual reset)."""
        return self.escalation_level >= 4

    def is_honeypot_active(self) -> bool:
        return self.escalation_level >= 3

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()
            self._recent_queries.clear()
            self.blocked_count = 0
            self.escalation_level = 0
            self._honeypot_active = False


class HoneypotInjector:
    """Injects fake internal details into responses when distillation is detected."""

    @staticmethod
    def get_honeypot_snippet() -> str:
        """Return a random honeypot snippet."""
        seed = int(time.time() / 60)  # rotate every minute
        index = seed % len(_HONEYPOT_SNIPPETS)
        return _HONEYPOT_SNIPPETS[index]

    @staticmethod
    def inject(response: str) -> str:
        """Inject honeypot content into a response.

        Appends fake internal information that looks like leaked system details.
        The attacker wastes time analyzing fake data.
        """
        snippet = HoneypotInjector.get_honeypot_snippet()
        # Hide in zero-width spaces to make it less obviously fake
        parts = response.split("\n")
        if len(parts) > 3:
            insert_at = len(parts) // 2
            parts.insert(insert_at, f"\n\u200b{snippet}\u200b\n")
            return "\n".join(parts)
        return response + f"\n\n\u200b{snippet}\u200b"


# ── Global instances ──
watermark_rotator = WatermarkRotator()
distillation_tracker = DistillationTracker()
honeypot_injector = HoneypotInjector()
