# LLM Layer

The `llm/` layer abstracts all AI model interactions. It provides a unified interface for calling multiple LLM providers (DeepSeek, OpenAI, Grok, Ollama) with role-based model selection, streaming support, and automatic fallback.

## Module Inventory

### Controller (`controller.py`)

> Centralized LLM orchestration — the single entry point for all model calls.

```python
class ModelsController:
    async def call(messages, role="default", temperature=None, tools=None,
                   tool_choice="auto", **kwargs) → _NormalizedResponse
    async def call_stream(messages, role="default", temperature=None, tools=None,
                          tool_choice="auto", **kwargs) → AsyncGenerator[StreamChunk, None]
```

#### Role-based model selection

Models are selected by **role** via `settings.model_roles`:

```python
model_roles = {
    "default":    {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.7},
    "fast":       {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.3},
    "reasoning":  {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.0},
    "agent":      {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.7},
    "creative":   {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.9},
    "critique":   {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.0},
}
```

The `role` parameter passed to `call()` or `call_stream()` selects the model configuration. Unknown roles fall back to `"default"`.

#### call() — Non-streaming

```python
async def call(self, messages, role="default", temperature=None, tools=None, **kwargs):
```

Flow:
1. Check circuit breaker for provider
2. Compress history if exceeding 90% `MAX_CONTEXT_TOKENS`
3. Get client from `LLMProvider.get_client(role, temperature)`
4. Call `client.chat.completions.create(model=..., messages=..., tools=...)`
5. On success: record success on circuit breaker, track cache metrics
6. On failure: retry with exponential backoff (up to `llm_max_retries`), then fallback to Ollama
7. Return normalized `_NormalizedResponse`

#### call_stream() — Streaming

```python
async def call_stream(self, messages, role="default", temperature=None, tools=None, **kwargs):
```

Returns `AsyncGenerator[StreamChunk, None]` yielding:

```python
@dataclass
class StreamChunk:
    text: str | None              # Content delta
    tool_name: str | None         # Tool call function name
    tool_arguments: str | None    # Tool call arguments JSON fragment
    tool_call_id: str | None      # Tool call identifier
    finish_reason: str | None     # "stop", "tool_calls", "length"
    reasoning_content: str | None # DeepSeek R1 reasoning (if model supports)
    usage: dict | None            # Token usage in final chunk
    is_done: bool                 # True on terminal chunk
```

Streaming flow:

1. Circuit breaker and context checks (same as `call()`)
2. Get async client from provider
3. Call `client.chat.completions.create(stream=True, stream_options={"include_usage": True})`
4. Iterate SSE chunks, accumulating tool calls by index
5. Yield `StreamChunk` objects for each delta (text or tool_call)
6. On failure: retry with backoff, then fallback to non-streaming `call()` — yield a single text chunk

#### Tool call accumulation

DeepSeek sometimes sends tool call chunks **without** an `index` field. The controller handles this:

```python
# llm/controller.py:390-415
# Tool calls are accumulated by index. If no index, append to the first tool call.
# After accumulation, filter empty tool calls that have no function name.
```

#### Normalized response

```python
@dataclass
class _NormalizedResponse:
    choices: list[_Choice]

@dataclass
class _Choice:
    message: _Message
    finish_reason: str = "stop"

@dataclass
class _Message:
    content: str | None
    tool_calls: list[dict] | None
```

All provider responses are normalized to this format before returning to callers.

### Provider (`provider.py`)

> Multi-provider client factory with automatic fallback.

```python
class LLMProvider:
    @classmethod
    def get_provider_name(cls, role: str) → str
    @classmethod
    def get_client(cls, role: str, temperature=None, force_ollama=False)
    @classmethod
    def get_async_client(cls, role: str, temperature=None, force_ollama=False)
```

#### Provider selection logic

1. **Check offline**: If `force_ollama`, `settings.offline_mode`, or `OfflineManager.is_offline()` — use Ollama
2. **Check role config**: Read `settings.model_roles[role]` for provider, model, temperature
3. **Check API key**: If the configured provider has no API key, skip to next
4. **Check circuit breaker**: If breaker is OPEN for provider, skip to fallback
5. **Create client**: Return configured `AsyncOpenAI` (or sync `OpenAI`) client

#### Provider matrix

| Provider | API Base | Client | Auth |
|----------|----------|--------|------|
| **DeepSeek** | `https://api.deepseek.com` (or `/beta` in strict mode) | `AsyncOpenAI` | `DEEPSEEK_API_KEY` |
| **OpenAI** | `https://api.openai.com/v1` | `AsyncOpenAI` | `OPENAI_API_KEY` |
| **Grok** | `https://api.x.ai/v1` | `AsyncOpenAI` | `GROK_API_KEY` |
| **Ollama** | `{OLLAMA_BASE_URL}/v1` | `AsyncOpenAI` | None |
| **Google** | `https://generativelanguage.googleapis.com/v1beta/openai/` | `AsyncOpenAI` | `GOOGLE_API_KEY` |

!!! note "DeepSeek Strict Mode"
    When `DEEPSEEK_STRICT_MODE=true`, the API base switches to `https://api.deepseek.com/beta` and tools use strict schemas where required parameters are enforced.

#### HTTP client configuration

```python
httpx.Timeout(
    connect=connect_timeout,        # From settings.llm_timeout / 3
    read=connect_timeout * 3,       # Allow for long streaming responses
    write=30,
    pool=5,
)
```

### Parser (`parser.py`)

> Robust JSON extraction from LLM responses.

```python
def parse_json_from_llm(text: str, default: Any = None) → dict:
```

**Extraction strategies** (tried in order):

1. `json.loads()` on the full text
2. Extract ` ```json ... ``` ` or ` ``` ... ``` ` fenced blocks
3. Find and parse the first balanced `{ ... }` using `json.JSONDecoder.raw_decode()`
4. `ast.literal_eval()` as last resort
5. Return `default` value

This robust parser handles the reality that LLMs often wrap JSON in markdown fences, add explanatory text before/after, or include trailing commas. Used by `MemoryManager._parse_critique_response()`, the decomposer, and other LLM-calling components.

### Prompts (`prompts.py`)

> Centralized prompt templates for agents and workflows.

```python
DECOMPOSE_TASK_PROMPT               # Standard task decomposition (3-5 subtasks)
DECOMPOSE_TASK_WITH_PHASES_PROMPT   # Phase-organized decomposition for coordinated workflows
ANALYZE_TASK_PROMPT                 # Task analysis for routing decisions
SUPERVISE_PROMPT                    # Supervisor review of agent outputs
AGGREGATE_PROMPT                    # Final aggregation of subtask results
```

Prompts use `{placeholder}` variables for runtime substitution (`{query}`, `{project_context}`, etc.). The prompt system ensures consistency across all LLM interactions — agent execution, workflow orchestration, and memory critique all use the same prompt source.

Key conventions:

- Clear output format instructions (e.g., "Responde SOLO con JSON válido")
- Specific file-naming requirements for subtasks (never generic like "create the code")
- Project context injection for existing codebases
- Follow-up awareness for conversation continuity

### Offline (`offline.py`)

> Offline mode detection and fallback control.

```python
class OfflineManager:
    async def detect() → bool         # Probe internet connectivity (Google, x.ai)
    def is_offline() → bool            # Check cached state (no I/O)
    def toggle_offline() → bool        # Toggle offline mode on/off
```

#### Connectivity detection

Probes multiple endpoints (`https://www.google.com`, `https://x.ai`) with 3 retry attempts. Caches result for 5 minutes (`_check_interval = 300`).

#### Offline state

```python
def is_offline(self) → bool:
    return settings.offline_mode or (self._is_offline is True)
```

Two paths to offline:
1. **User-forced**: `OFFLINE_MODE=true` in `.env` or `settings.offline_mode = True`
2. **Auto-detected**: Connectivity check fails — cached for 5 minutes

When offline, `LLMProvider.get_client()` returns an Ollama client regardless of the configured role provider.

## Module loader

```python
# llm/__init__.py
from llm.controller import ModelsController
from llm.parser import parse_json_from_llm

models = ModelsController()  # Global instance used throughout the codebase
```

The global `models` instance is imported by agents, orchestrators, and memory — it is the single point of LLM access.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────┐
│ Callers (agents, orchestrator, memory, decomposer)    │
└──────────────────────┬───────────────────────────────┘
                       │ models.call() / models.call_stream()
                       ▼
┌──────────────────────────────────────────────────────┐
│ ModelsController (llm/controller.py)                   │
│   • Role-based model selection                         │
│   • Circuit breaker check                              │
│   • Context compression                                │
│   • Retry with exponential backoff                     │
│   • Fallback: stream → non-stream → Ollama             │
│   • Tool call accumulation by index                    │
│   • Response normalization                             │
└──────────────────────┬───────────────────────────────┘
                       │ provider.get_client()
                       ▼
┌──────────────────────────────────────────────────────┐
│ LLMProvider (llm/provider.py)                          │
│   • Provider detection: DeepSeek → OpenAI → Grok →     │
│     Google → Ollama (fallback)                         │
│   • Offline check → force Ollama                       │
│   • Circuit breaker integration                        │
│   • API key validation                                 │
│   • httpx client with timeout + pool config            │
└──────┬───────────┬──────────┬───────────┬─────────────┘
       │           │          │           │
       ▼           ▼          ▼           ▼
   ┌───────┐  ┌───────┐  ┌───────┐  ┌────────┐
   │DeepSeek│ │OpenAI │  │ Grok  │  │ Ollama │
   │  API  │  │  API  │  │  API  │  │ local  │
   └───────┘  └───────┘  └───────┘  └────────┘
       ▲
       │ OFFLINE_MODE=true or connectivity loss
       │ triggers fallback
       └────────────────────────────────────────────────┐
                                                        │
┌──────────────────────────────────────────────────────┐│
│ OfflineManager (llm/offline.py)                       ││
│   • Probe: google.com, x.ai (3 retries)               ││
│   • Cache result 5 min                                ││
│   • toggle_offline() for user control                 │◀┘
└──────────────────────────────────────────────────────┘
```

## Key Design Decisions

1. **Single controller**: All LLM calls go through `models.call()` / `models.call_stream()` — no scattered API usage.

2. **Role-based configuration**: Adding a new model for a specific purpose is a config change, not a code change.

3. **Graceful degradation**: Stream failure → non-stream fallback → provider fallback → Ollama fallback. The system always has a path to produce output.

4. **Normalized responses**: All providers produce the same response format — callers never need provider-specific logic.

5. **Tool call robustness**: Accumulation handles the edge case where DeepSeek omits the tool call `index` field, and filters empty tool calls before returning to the orchestrator.
