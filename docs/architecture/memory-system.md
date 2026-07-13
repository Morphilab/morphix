# Memory System

Morphix uses a three-layer self-healing memory system with FAISS-based semantic search, LLM-driven quality critique, and a background daemon for continuous maintenance.

## Architecture Overview

```
MemoryManager (singleton)
    ├── FAISS IndexFlatL2 (1024-dim vectors)
    ├── EmbeddingProvider (SentenceTransformers)
    ├── Per-workspace isolation (memory/{workspace}/)
    └── autoDream daemon (self_healing_check)
```

## MemoryManager

**File:** `core/memory/manager.py`

Singleton that manages all memory operations — store, search, update, delete — with quality validation and automatic correction.

### Core API

```python
# Write (with quality validation)
await memory.write("key", value, validated=False, content_hint="analytical")

# Read by key
value = memory.read("key")

# Semantic search (FAISS)
results = memory.search("query", k=5, min_similarity=0.0)
# Returns: [{"key": "...", "value": ..., "distance": 0.34, "similarity": 0.7463}, ...]

# Write to system-global space (not workspace-scoped)
await memory.write_system("key", value)

# User profile management
profile = memory.get_user_profile()
await memory.update_user_profile({"name": "Alice", "country": "ES"})

# Correction tracking
await memory.save_user_correction(original_task, correction)
```

### Workspace isolation

Memory is isolated per workspace. Each workspace has its own subdirectory under `memory/`:

```
memory/
├── main/           # Default workspace
│   ├── user_profile.md
│   ├── last_creative_output.md
│   └── ...
├── system/         # Global (cross-workspace)
│   └── ...
└── myworkspace/    # Custom workspace
    └── ...
```

Switching workspaces:

```python
await memory.switch_workspace("myworkspace")
```

On switch, the FAISS index and document list are atomically swapped. Embedding computation runs in a thread pool to avoid blocking the event loop.

### Quality validation pipeline

Every `write()` that is not pre-validated goes through LLM critique:

```python
async def write(self, key, value, validated=False, content_hint=None) -> bool:
    if not validated:
        critique = await self._llm_critique(key, value, content_hint)
        score = critique["quality_score"]  # 0-100

        if score < threshold:
            return False  # Rejected

        if critique.get("suggested_fix"):
            value = critique["suggested_fix"]  # Auto-corrected

    # Compute embedding → persist to disk → add to FAISS index
```

**Quality thresholds** vary by content type:

| Content type | Threshold |
|-------------|-----------|
| `user_profile_last_update` | 15 |
| `workflow_subtask_*` | 20 |
| `creative` hint | 30 |
| `analytical` hint | 50 |
| Default | 40 |

### Write rollback

Writes use a transactional pattern: if the file write or index update fails after removing the old entry, the old entry is restored (both in the index and on disk).

### Protected keys

Certain keys are protected from automatic modification/deletion:

```python
_PROTECTED_EXACT = {
    "kairos_daemon_heartbeat",
    "user_profile",
    "user_profile_last_update",
    "security_private",
    "last_creative_output",
    "last_analysis",
    "last_plan",
    "last_connection",
    "last_successful_code",
}
_PROTECTED_PREFIXES = ("workflow_subtask_", "last_", "merged_")
```

## FAISS Indexer

**File:** `core/faiss_indexer.py`

Reusable FAISS indexer with save/load support.

```python
FAISS_DIMENSION = 1024  # Matches multilingual-e5-large output dimension

class FAISSIndexer:
    def __init__(self, dimension=1024, embedder=None)
    def add(key: str, value: object) -> None
    def search(query: str, k: int = 5) -> list[dict]
    def remove(key: str) -> None
    def rebuild_index() -> None
    def clear() -> None
    def save(directory: Path) -> None       # Persist to faiss.index + documents.pkl
    def load(directory: Path) -> FAISSIndexer  # Restore from disk
```

Embedding computation delegates to `EmbeddingProvider`. The index uses `IndexFlatL2` (exact L2 search — no approximation, suitable for small-to-medium document counts).

## Embedding Provider

**File:** `core/embedding_provider.py`

Lazy-loading embedding provider using SentenceTransformers.

```python
class EmbeddingProvider:
    _model_name = "intfloat/multilingual-e5-large"  # 1024-dim

    @classmethod
    def get_instance(cls):    # Returns model or None if not yet loaded
    @classmethod
    def encode(cls, text):     # Encode text (returns None if model not ready)
    @classmethod
    def wait_until_ready(cls, timeout=60) -> bool:  # Block until model loaded
```

The model is loaded in a background daemon thread — the application starts immediately, and memory operations gracefully degrade until the model is ready.

!!! tip "Model choice"
    `multilingual-e5-large` was chosen for its strong multilingual support (English + Spanish), 1024-dimensional embeddings, and permissive license.

## autoDream Daemon — Self-Healing

**File:** `core/memory/manager.py:538`

The `MemoryManager.self_healing_check()` method implements an autonomous memory maintenance daemon. It runs periodically when `DAEMON_MODE=true`.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `SELF_HEAL_INTERVAL` | 120s | Time between self-healing cycles |
| `DAEMON_MODE` | `false` | Enables the background daemon task |
| `DAEMON_MODE=false` (CI) | — | Skips daemon entirely |

### Self-healing phases

The daemon runs four sequential phases:

#### Phase 1: Quality critique

Checks the 20 most recent documents. Any with `quality_score < 60` are:
- **Auto-corrected** if the LLM suggests a fix
- **Deleted** if no fix can be generated

Protected keys (see above) are skipped.

#### Phase 2: Duplicate detection

```python
# core/memory/manager.py:324 — _detect_duplicates
```

Uses FAISS similarity search to find document pairs with **similarity > 0.92**. When found:
1. Both documents are sent through LLM critique
2. The higher-quality document is kept
3. The lower-quality document is deleted from disk and index

#### Phase 3: Contradiction resolution

```python
# core/memory/manager.py:392 — _resolve_contradictions
```

Detects document pairs with **similarity 0.65–0.92** (similar enough to be related, different enough to potentially contradict). These are sent to an LLM arbitrator:

```python
async def _arbitrate_contradiction(key_a, val_a, key_b, val_b) -> str | None:
    """Ask LLM: 'Do these contradict? If so, produce a single consolidated fact.'"""
```

If the LLM detects a contradiction, both originals are deleted and replaced with a `merged_` document. If no contradiction is found (LLM returns `SKIP`), both documents are preserved.

#### Phase 4: Stale pruning

```python
# core/memory/manager.py:487 — _prune_stale
```

Removes documents not accessed in **30+ days** (skipping protected keys). The `_access_log` dictionary tracks last access timestamps, updated on every `read()` and `search()` call.

### Index rebuild

After any phase that modifies the document set, the FAISS index is rebuilt atomically:

1. Document snapshot taken under lock
2. Embeddings pre-computed outside lock (slow operation)
3. New `IndexFlatL2` created and populated under lock

## Integration with Conversations

Memory integrates with the conversation system in two ways:

1. **Context augmentation**: Before sending messages to the LLM, `get_user_summary()` and `get_long_context_summary()` extract relevant memory facts and inject them into the prompt context.

2. **Security logging**: Blocked distillation attempts are written to memory key `"security_private"` via `undercover._block_attempt()` — creating a persistent audit trail that survives application restarts.

## Directory Structure

```
memory/
├── system/                    # Global, cross-workspace
│   └── kairos_daemon_heartbeat.md
├── main/                      # Default workspace
│   ├── user_profile.md
│   ├── last_creative_output.md
│   ├── security_private.md
│   └── ...
├── myproject/                 # Custom workspace
│   └── ...
└── ...
```
