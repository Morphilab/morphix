# Security Model

Morphix uses a layered security model that combines runtime protection,
sandboxed code execution, behavioral defenses and data-handling controls. The
goal is to keep the platform useful under failure, hostile input and model
extraction attempts without turning the documentation into an operational
playbook.

## Threat model

| Threat | Primary control |
|--------|-----------------|
| Runaway LLM cost or provider quota exhaustion | Rate limiter |
| Cascading failures / provider outage | Circuit breaker + Ollama fallback |
| Arbitrary code execution from user or model | RestrictedPython sandbox in a confined child process |
| System-prompt / architecture extraction | Undercover mode |
| Model distillation through repeated queries | Anti-distillation tracker |
| Indirect prompt injection from tool output | Untrusted-data wrapping + response injection scan |
| User frustration / repeated identical queries | Frustration detector + calming prompt |

## Runtime protection

### Circuit breaker

`core/circuit_breaker.py` implements the classic three-state breaker per LLM
provider:

```
CLOSED → OPEN → HALF_OPEN → CLOSED
```

- **CLOSED**: requests pass through.
- **OPEN**: requests are rejected immediately.
- **HALF_OPEN**: a single probe is allowed after the recovery window.

The controller checks the breaker for both streaming and non-streaming calls.
When the active provider is open, the request falls back to Ollama instead of
cascading failures. The registry keeps one breaker per provider and exposes
their states for diagnostics.

### Rate limiter

`core/rate_limiter.py` uses a sliding window to cap LLM consumption per minute
and per hour. It prevents runaway cost and respects provider quotas. Callers can
try to acquire a slot immediately, wait for one with a timeout, or inspect the
remaining capacity. The global limiter is initialized lazily from settings.

## Code execution sandbox

`core/sandbox/restricted_executor.py` runs user-requested code through
**RestrictedPython** inside a confined child process.

Design properties:

- AST parsing and REPL-style evaluation of the final expression.
- Strict module and builtin allowlists; dangerous modules are not importable.
- The child process does not import `core.*`, so it does not inherit settings,
  database credentials or API keys.
- Memory and wall-clock limits are enforced on the child.
- Timeouts terminate the child; the host does not keep zombie threads.
- `print()` output is captured and bounded.
- Matplotlib output is written to `charts/` and referenced from the result.
- Inline Python through the shell tool is blocked by policy; code execution
  must go through the sandbox.

The exact allowlists and limits are implementation details and are intentionally
not reproduced here.

## Behavioral security

### Undercover mode

`core/security/undercover_mode.py` reduces extraction of internal system
details (prompts, architecture, tool configuration). It is controlled by
`UNDERCOVER_MODE`.

Detection works at three levels:

1. **Phrase matching** for explicit extraction requests.
2. **Jailbreak-pattern matching** for instruction-override attempts, including
   Spanish variants.
3. **Similarity tracking** for repeated attempts to rephrase the same request.

Response protection includes:

- **Redaction** of internal terms in model output.
- **Injection scan** for indirect prompt-injection patterns coming from tool
  output.
- **Decoy/honeypot content** at high escalation levels.
- **Watermarking** with a rotating trace.
- **Throttling** under sustained extraction pressure.
- **Identity enforcement** through a hardened identity prompt.

Exact phrases, thresholds and escalation timings are not published.

### Anti-distillation

`core/security/anti_distillation.py` hardens the system against model
extraction:

- **Watermark rotation**: several trace styles rotate over time and vary by
  workspace; the trace is bound to the output content.
- **Distillation tracker**: keeps a short history of recent queries and flags
  clusters of similar requests, which is the signature of iterative extraction.
- **Escalation**: warn → throttle → honeypot → lock. Escalation de-escalates
  after an idle period instead of locking a session forever.

The exact thresholds, queue sizes and decoy mechanics are implementation
details and are intentionally omitted.

### Frustration detector

`core/security/frustration_detector.py` watches for frustration signals such as
repeated commands, shouting, repeated complaints and repeated identical
queries. When detected, it logs the event and injects a calming prompt modifier
so the next agent response is more patient and step-by-step.

The exact regular expressions and counters are not published.

## Subsystem interactions

```
User query
    │
    ▼
Frustration detector ──► calming prompt modifier
    │
    ▼
Undercover mode ───────► block / allow
    │
    ▼
Rate limiter ──────────► allow / wait
    │
    ▼
Circuit breaker ───────► allow / fallback to Ollama
    │
    ▼
LLM call
    │
    ▼
Safe response ─────────► redact, watermark, decoy content
    │
    ▼
Response
```

## What is intentionally not public

Exact blocked-phrase lists, jailbreak regexes, similarity thresholds, attempt
counters, throttle delays, honeypot payloads and watermark styles are
operational details. They live in the implementation and its tests; this page
documents the controls and their purpose without reproducing them.

## Reporting a vulnerability

See [SECURITY.md](https://github.com/morphilab/morphix/blob/main/.github/SECURITY.md)
for the security policy and the private reporting channel.
