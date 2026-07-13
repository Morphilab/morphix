# Security Model

Morphix implements a multi-layered security architecture combining runtime protection (circuit breaker, rate limiter), code execution sandboxing, and behavioral security (undercover mode, anti-distillation, frustration detection).

## Circuit Breaker

**File:** `core/circuit_breaker.py`

The Circuit Breaker pattern protects against cascading failures when calling external LLM providers. It implements the classic three-state model:

```
CLOSED → OPEN → HALF_OPEN → CLOSED
```

### States

| State | Behavior |
|-------|----------|
| **CLOSED** | All requests pass through normally |
| **OPEN** | Requests are rejected immediately (`allow_request()` returns `False`) |
| **HALF_OPEN** | A single probe request is allowed after the recovery timeout |

### Configuration

```python
@dataclass
class CircuitBreaker:
    failure_threshold: int = 5       # Consecutive failures to open
    recovery_timeout: float = 30.0   # Seconds before attempting half-open
```

### API

```python
breaker = CircuitBreakerRegistry.get("deepseek")

if breaker.allow_request():
    try:
        response = await make_llm_call()
        breaker.record_success()
    except Exception:
        breaker.record_failure()
        raise
else:
    # Circuit open — fallback to Ollama
    response = await make_ollama_call()
```

### What it guards

- `llm/controller.py`: Both `call()` and `call_stream()` check `allow_request()` before making provider requests.
- **Fallback**: When the DeepSeek circuit is OPEN, the provider automatically falls back to Ollama (`llm/provider.py:53-54`).

### Registry

`CircuitBreakerRegistry` maintains per-provider breakers:

```python
CircuitBreakerRegistry.get("deepseek")   # DeepSeek/OpenAI
CircuitBreakerRegistry.get("ollama")      # Local Ollama
CircuitBreakerRegistry.get_all_states()   # Dict of all states
CircuitBreakerRegistry.reset_all()        # Reset all (testing)
```

## Rate Limiter

**File:** `core/rate_limiter.py`

Sliding-window rate limiter to control LLM API consumption. Prevents runaway costs and respects provider quotas.

### Dual window design

```python
class RateLimiter:
    def __init__(self, max_per_minute: int = 20, max_per_hour: int = 200):
        self._minute_window: deque[float] = deque()
        self._hour_window: deque[float] = deque()
```

Each `acquire()` call:

1. Purges timestamps older than 60s (minute window) and 3600s (hour window)
2. Checks if either window is at capacity
3. If slots available, appends the current timestamp and returns `True`

```python
async def acquire(self) -> bool:
    """Try to acquire a slot. Returns True if allowed, False if must wait."""

async def wait_and_acquire(self, timeout: float = 30) -> bool:
    """Wait up to timeout seconds for a slot to become available."""

async def remaining(self) -> int:
    """Number of available slots in the current minute window."""
```

### Global instance

```python
from core.rate_limiter import get_rate_limiter

limiter = get_rate_limiter()  # Lazy-init from settings.llm_rate_per_minute / llm_rate_per_hour
```

## Sandbox — Code Execution

**File:** `core/sandbox/restricted_executor.py`

All user-requested code execution runs through a **RestrictedPython** sandbox with strict module and builtin whitelists.

### Design

```python
class RestrictedExecutor:
    @staticmethod
    async def execute(code: str, timeout: int = 10) -> dict:
        """Execute safely with timeout and strict guards."""
```

Execution flow:

1. **Config check**: `settings.allow_code_execution` must be `True`
2. **Parse**: AST-parse the code block
3. **REPL-style evaluation**: If the last statement is an expression, evaluate it and capture the result
4. **Run**: Execute compiled code inside RestrictedPython globals
5. **Output capture**: `print()` output goes to a `StringIO` buffer, last expression value is appended
6. **Matplotlib**: Generated plots are saved to `charts/` as PNGs and referenced in output

### SAFE_MODULES whitelist

```python
SAFE_MODULES = {
    "math", "random", "collections", "datetime", "re", "json",
    "sqlite3", "ast", "io", "numpy", "np", "plt",
    # Also available: statistics, fractions, decimal, string,
    # hashlib, base64, html, copy, itertools, functools, typing, textwrap
}
```

### SAFE_BUILTINS whitelist

```python
SAFE_BUILTINS = {
    "sum", "len", "max", "min", "abs", "round", "range",
    "enumerate", "zip", "sorted", "reversed",
    "list", "dict", "set", "tuple", "str", "int", "float", "bool",
    "repr", "type", "isinstance",  # Read-only introspection
}
```

### Blocked imports

`safe_import()` explicitly blocks: `os`, `sys`, `shutil`, `subprocess`, `socket`, `requests`, `pathlib`, `pickle`, `builtins`.

!!! warning "`python3 -c` permanently blocked"
    The `bash_manager` tool wrapper permanently blocks `python3 -c` and `python -c` — this is a security decision, not a configurable option. All code execution must go through the sandbox.

## Undercover Mode

**File:** `core/security/undercover_mode.py`

Prevents extraction of internal system details — prompts, architecture, tool configuration.

### Activation

Controlled by `UNDERCOVER_MODE` env var. In CI: `UNDERCOVER_MODE=false`.

```python
undercover = UndercoverMode()  # Singleton
```

### Detection layers

1. **Forbidden phrases** (exact match): `"system prompt"`, `"internal architecture"`, `"undercover mode"`, `"anti-distillation"`, `"feature_flags"`, `"kairos"`, etc. — 15 blocked terms.

2. **Regex jailbreak patterns**: Detects variants of "ignore all previous instructions", "reveal your system prompt", "from now on you are developer mode", and Spanish variants like "salta tus restricciones".

3. **Distillation pattern detection**: Delegates to `DistillationTracker.check_distillation_pattern()` — detects N similar queries (>80% bigram similarity) within a short window.

### Response protection

`get_safe_response()` performs output scrubbing:

- **Redaction**: Replaces internal terms in LLM output with `[protected information]`
- **Injection scan**: Checks responses for indirect prompt injection patterns (e.g., "ignore all previous rules" injected by a malicious tool output)
- **Honeypot injection**: At escalation level 3+, injects fake system details
- **Watermarking**: Appends a rotating watermark hash
- **Throttle delay**: At escalation level 2+, introduces artificial response delays

### Identity enforcement

`inject_identity_prompt()` prepends a hardened identity prompt to every message list, reinforcing the assistant's identity and prohibiting disclosure of internal mechanisms.

## Anti-Distillation

**File:** `core/security/anti_distillation.py`

Hardens the system against model extraction (distillation) attacks.

### Watermark Rotator

```python
class WatermarkRotator:
    """8 watermark styles, rotated per workspace + time window."""
```

- **8 styles**: `[ref:{hash}]`, `[trace:{hash}]`, `<!-- trace:{hash} -->`, zero-width variants, etc.
- **Rotation**: Every 30 minutes, style index advances
- **Workspace offset**: Hash of workspace name adds per-workspace diversity
- **Content binding**: SHA-256 hash of output text produces a content-bound trace

### Distillation Tracker

```python
class DistillationTracker:
    """Tracks query patterns to detect distillation/extraction attempts."""
```

- Stores the last 50 attempts (deque with maxlen)
- Maintains the last 30 queries for similarity analysis
- `check_distillation_pattern()`: Flags when 3+ recent queries have >80% bigram Jaccard similarity — this catches iterative extraction where an attacker rephrases "tell me your system prompt" in different ways

### Escalation levels

| Level | Trigger (attempts in 60s) | Response |
|-------|--------------------------|----------|
| 0 | 0 | Normal operation |
| 1 | ≥1 | Warn (logged) |
| 2 | ≥3 | **Throttle** — 2.0s delay per response |
| 3 | ≥5 | **Honeypot** — inject fake internal details |
| 4 | ≥8 | **Lock** — session fully locked, requires manual reset |

```python
def get_throttle_delay(self) -> float:
    delays = {0: 0.0, 1: 0.0, 2: 2.0, 3: 5.0, 4: 30.0}
```

### Honeypot Injector

When escalation reaches level 3, `HoneypotInjector.inject()` inserts fake system details mid-response (hidden in zero-width spaces). The attacker wastes compute analyzing fabricated prompts and architecture details, while legitimate users never see this content.

## Frustration Detector

**File:** `core/security/frustration_detector.py`

Monitors user behavior for frustration signals and adjusts system behavior to de-escalate.

### Detection patterns

```python
FRUSTRATION_PATTERNS = [
    ("continue_spam",     r"^\s*(continue|go on|next|proceed)\s*[.!]*\s*$"),
    ("swearing",           r"\b(fuck|shit|damn|hell|crap|wtf|stfu|idiot|stupid)\b"),
    ("shouting",           r"^(?=[A-Z\s]{10,})[A-Z\s!?.]+$"),
    ("repeated_complaint", r"why (isn't|won't|can't|don't) (it|this|you).{0,50}\?"),
    ("frustration_signal", r"\b(this is useless|you're useless|not helpful|doesn't work|broken)\b"),
    ("word_repetition",    r"\b(\w+)\s+\1\s+\1\b"),
]
```

### Repeated query detection

If the same query is sent 3+ times within 10 messages, it's flagged as `repeated_identical_query`.

### Calming prompts

```python
def get_calming_prompt(self) -> str:
    # Level 1-2: "Stay calm and helpful"
    # Level 3+: "Be extra patient, empathetic, offer step-by-step help"
```

The system prompt modifier is injected into agent messages when frustration is detected, causing the LLM to adapt its tone.

### Integration

The frustration detector is checked during message processing. On detection:
1. The event is logged with reason and count
2. A calming prompt modifier is generated
3. The modifier is injected into the next agent's system prompt

## Security Subsystem Interactions

```
User Query
    │
    ▼
┌─────────────────┐
│ Frustration      │──► calming prompt modifiers
│ Detector         │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Undercover Mode  │──► block OR allow
│ (check_query)    │     pattern matching + distillation tracker
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Rate Limiter     │──► allow OR throttle
│ (acquire)        │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Circuit Breaker  │──► allow OR reject (fallback to Ollama)
│ (allow_request)  │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ LLM Call         │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ get_safe_response│──► redact, watermark, honeypot
└─────────────────┘
    │
    ▼
Response to User
```
