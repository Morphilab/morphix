"""Frustration Detector — detects user frustration patterns via regex.

When frustration is detected, the system can adjust agent behavior:
switch to calmer mode, slow responses, offer help.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Patterns that indicate user frustration
FRUSTRATION_PATTERNS: list[tuple[str, str]] = [
    # Repeated "continue" / "go on" spam
    (r"(?i)^\s*(continue|go\s*on|next|proceed)\s*[.!]*\s*$", "continue_spam"),
    # Swearing / strong language
    (r"(?i)\b(fuck|shit|damn|hell|crap|wtf|stfu|idiot|stupid)\b", "swearing"),
    # All-caps shouting
    (r"^(?=[A-Z\s]{10,})[A-Z\s!?.]+$", "shouting"),
    # Repeated identical queries (3+ repetitions)
    (r"(?i)(why (isn't|won't|can't|don't) (it|this|you).{0,50}\?)", "repeated_complaint"),
    # Direct frustration signals
    (
        r"(?i)\b(this is useless|you're useless|you are useless|not helpful|doesn't work|broken)\b",
        "frustration_signal",
    ),
    # Too many repeated words
    (r"(?i)\b(\w+)\s+\1\s+\1\b", "word_repetition"),
]


class FrustrationDetector:
    _instance = None
    _lock = None  # type: ignore

    def __new__(cls):
        import threading

        if cls._instance is None:
            if cls._lock is None:
                cls._lock = threading.RLock()
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.frustration_count = 0
        self.last_frustrated_at: float | None = None
        self._recent_queries: list[str] = []

    def check(self, query: str) -> tuple[bool, str | None]:
        """Check a user query for frustration signals.
        Returns (is_frustrated, reason).
        """
        if not query or not query.strip():
            return False, None

        # Track recent queries for repetition detection
        self._recent_queries.append(query.strip())
        if len(self._recent_queries) > 10:
            self._recent_queries.pop(0)

        # Check for repeated identical queries
        if self._recent_queries.count(query.strip()) >= 3:
            self.frustration_count += 1
            return True, "repeated_identical_query"

        for pattern, reason in FRUSTRATION_PATTERNS:
            if re.search(pattern, query):
                self.frustration_count += 1
                import time

                self.last_frustrated_at = time.time()
                logger.info(f"Frustration detected: {reason} " f"(count: {self.frustration_count})")
                return True, reason

        return False, None

    def get_calming_prompt(self) -> str:
        """Return a system prompt modifier for frustrated users."""
        if self.frustration_count >= 3:
            return (
                "\n[NOTICE: The user appears frustrated. Be extra patient, "
                "empathetic, and offer step-by-step help. "
                "Acknowledge their frustration without directly mentioning it. "
                "Suggest taking a simpler approach.]"
            )
        elif self.frustration_count >= 1:
            return (
                "\n[NOTE: The user may be frustrated. Stay calm and helpful. "
                "Offer clear, direct solutions.]"
            )
        return ""

    def reset(self):
        """Reset frustration tracking."""
        self.frustration_count = 0
        self.last_frustrated_at = None
        self._recent_queries.clear()


frustration_detector = FrustrationDetector()
