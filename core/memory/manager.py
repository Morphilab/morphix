"""
MemoryManager - Sistema de 3 Capas Self-Healing (VERSIÓN FINAL ROBUSTA Y ESTABLE)
Aislamiento por workspace: subdirectorios memory/{workspace}/
"""

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any

import faiss

from core.embedding_provider import EmbeddingProvider
from core.faiss_indexer import FAISS_DIMENSION
from core.utils import clean_llm_response  # noqa: F401 — re-exported for backward compat
from llm import models, parse_json_from_llm

logger = logging.getLogger(__name__)


class MemoryManager:
    _PROTECTED_EXACT: set[str] = {
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
    _PROTECTED_PREFIXES: tuple[str, ...] = ("workflow_subtask_", "last_", "merged_")

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_memory()
        return cls._instance

    def _init_memory(self):
        from core.path_resolver import paths

        self.base_dir = paths.memory_base()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.active_workspace = None
        self.documents: list[tuple[str, Any]] = []
        self.index = faiss.IndexFlatL2(FAISS_DIMENSION)
        self.embedder = EmbeddingProvider  # lazy — carga en background
        self._access_log: dict[str, float] = {}  # key -> last access timestamp
        logger.info("✅ Memoria 3-capas inicializada (embeddings en carga lazy)")

    def _embed(self, text: str):
        """Wrapper que espera al modelo si aún no está listo."""
        if not self.embedder.wait_until_ready(timeout=60):
            logger.warning("Modelo de embeddings no disponible tras timeout")
            return None
        return self.embedder.encode(text)

    # ==================== HELPERS ====================
    def get_user_summary(self) -> str:
        profile = self.get_user_profile()
        if not profile or not any(profile.values()):
            return ""
        lines = [
            f"- {k.replace('_', ' ').title()}: {v}"
            for k, v in profile.items()
            if v and k != "preferences"
        ]
        return "\n".join(lines)

    def get_long_context_summary(self, history: list, max_facts: int = 8) -> str:
        if len(history) <= 10:
            return ""
        facts = []
        for msg in history[:-10]:
            content = msg.get("content", "").strip()
            if content and len(content) > 15:
                facts.append(content[:250])
        if not facts:
            return ""
        return "\n".join(f"- {f}" for f in facts[:max_facts])

    async def save_user_correction(self, original_task: str, correction: str) -> bool:
        key = f"correction_{hash(original_task) % 100000}"
        value = {
            "original": original_task[:300],
            "correction": correction[:800],
            "timestamp": int(time.time()),
        }
        return await self.write(key, value, validated=True, content_hint="analytical")

    # ==================== CAMBIO DE WORKSPACE ====================
    async def switch_workspace(self, workspace: str):
        """Switch to the given workspace, loading its documents and index.
        Embedding computation runs in a thread pool to avoid blocking the event loop."""
        with self._lock:
            if self.active_workspace == workspace:
                return

            old_ws = self.active_workspace
            old_docs = self.documents
            old_index = self.index

            try:
                ws_dir = self.base_dir / workspace
                ws_dir.mkdir(parents=True, exist_ok=True)

                # Read files under lock (fast I/O)
                file_entries: list[tuple[str, Any]] = []
                for file in ws_dir.glob("*.md"):
                    with open(file, encoding="utf-8") as f:
                        content = f.read().strip()
                    key = file.stem
                    val = json.loads(content) if content.startswith("{") else content
                    file_entries.append((key, val))
            except Exception as e:
                logger.warning("Unhandled exception in MemoryManager", exc_info=True)
                self.active_workspace = old_ws
                self.documents = old_docs
                self.index = old_index
                raise RuntimeError(f"Error switching to workspace '{workspace}': {e}")

        # Compute embeddings in thread pool (slow operation, non-blocking for async)
        def _build_index():
            new_index = faiss.IndexFlatL2(FAISS_DIMENSION)
            new_docs = []
            for key, val in file_entries:
                try:
                    emb = self._embed(str(val))
                    new_index.add(emb.reshape(1, -1))
                    new_docs.append((key, val))
                except Exception:
                    logger.warning("Error generating embedding for '%s', skipping", key)
            return new_index, new_docs

        new_index, new_docs = await asyncio.to_thread(_build_index)

        # Atomic swap of index and documents under lock
        with self._lock:
            self.active_workspace = workspace
            self.documents = new_docs
            self.index = new_index
            logger.info(f"🔄 Workspace switched to '{workspace}' ({len(new_docs)} documents)")

    # ==================== ESCRITURA EN SYSTEM (global) ====================
    async def write_system(self, key: str, value: Any) -> bool:
        """Escribe en memory/system/ sin interferir con el índice activo."""
        sys_dir = self.base_dir / "system"
        sys_dir.mkdir(exist_ok=True)
        file = sys_dir / f"{key}.md"
        with self._lock:
            try:
                with open(file, "w", encoding="utf-8") as f:
                    if isinstance(value, (dict, list)):
                        json.dump(value, f, indent=2, ensure_ascii=False)
                    else:
                        f.write(str(value))
                return True
            except Exception as e:
                logger.error(f"Error escribiendo en system/{key}: {e}")
                return False

    # ==================== ROBUST WRITE WITH ROLLBACK (FIXED) ====================
    async def write(
        self, key: str, value: Any, validated: bool = False, content_hint: str | None = None
    ) -> bool:
        if self.active_workspace is None:
            logger.error("No hay workspace activo. No se puede escribir en memoria.")
            return False

        score: int | str = "N/A"

        if not validated:
            critique = await self._llm_critique(key, value, content_hint)
            score = int(critique.get("quality_score", 0))  # type: ignore[no-redef]
            threshold: int = self._get_quality_threshold(content_hint, key)

            if score < threshold:
                logger.warning(f"❌ Write RECHAZADO: {key} (score: {score} < {threshold})")
                return False

            if critique.get("suggested_fix"):
                value = critique["suggested_fix"]
                logger.info(f"🔧 Auto-corrección aplicada a: {key}")

        # Pre-compute embedding OUTSIDE the lock to avoid blocking other
        # memory operations while the model generates the vector.
        embedding = self._embed(str(value))
        if embedding is None:
            logger.error(f"❌ Embedding no disponible para '{key}'")
            return False

        with self._lock:
            old_entry = next(((k, v) for k, v in self.documents if k == key), None)
            self.documents = [doc for doc in self.documents if doc[0] != key]

            ws_dir = self.base_dir / self.active_workspace
            ws_dir.mkdir(parents=True, exist_ok=True)
            file = ws_dir / f"{key}.md"
            file_created = False

            try:
                with open(file, "w", encoding="utf-8") as f:
                    if isinstance(value, (dict, list)):
                        f.write(json.dumps(value, indent=2, ensure_ascii=False))
                    else:
                        f.write(str(value))
                file_created = True

                self.index.add(embedding.reshape(1, -1))
                self.documents.append((key, value))
                self._access_log[key] = time.time()
            except Exception as e:
                logger.error(f"Error saving '{key}': {e}")
                # Always restore the previous entry
                if old_entry is not None:
                    self.documents.append(old_entry)
                    # Also restore the previous file content
                    try:
                        with open(file, "w", encoding="utf-8") as f:
                            old_val = old_entry[1]
                            if isinstance(old_val, (dict, list)):
                                json.dump(old_val, f, indent=2, ensure_ascii=False)
                            else:
                                f.write(str(old_val))
                    except Exception:
                        logger.debug("Rollback de archivo fallido en restauración", exc_info=True)
                    logger.info(f"↩️ Rollback completado para '{key}'")
                elif file_created and file.exists():
                    file.unlink()
                return False

        logger.info(f"✅ Memoria escrita: {key} (score: {score})")
        return True

    def _get_quality_threshold(self, content_hint: str | None, key: str) -> int:
        if key == "user_profile_last_update":
            return 15
        if key.startswith("workflow_subtask_"):
            return 20
        if content_hint == "creative":
            return 30
        if content_hint == "analytical":
            return 50
        return 40

    # ==================== LLM CRITIQUE ====================
    async def _llm_critique(self, key: str, value: Any, content_hint: str | None = None) -> dict:
        if not value or len(str(value).strip()) < 10:
            return {
                "quality_score": 0,
                "is_valid": False,
                "suggested_fix": "",
                "reason": "Contenido demasiado corto",
            }

        prompt = self._build_critique_prompt(key, value, content_hint)

        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="critique",
                temperature=0.0,
            )
            raw = clean_llm_response(response)
            data = self._parse_critique_response(raw)

            if not data:
                logger.warning(
                    f"⚠️ Parseo de crítica vacío para '{key}', usando valores por defecto"
                )
                data = {}

            return {
                "quality_score": int(float(data.get("quality_score", 50))),
                "is_valid": bool(data.get("is_valid", True)),
                "suggested_fix": data.get("suggested_fix", ""),
                "reason": data.get("reason", ""),
            }
        except Exception as e:
            logger.warning(f"Critique falló para '{key}': {e}")
            return {
                "quality_score": 60,
                "is_valid": True,
                "suggested_fix": "",
                "reason": f"Excepción: {e}",
            }

    def _build_critique_prompt(self, key: str, value: Any, content_hint: str | None = None) -> str:
        safe_value = str(value)[:1000]
        tipo = {
            "creative": "contenido CREATIVO",
            "analytical": "análisis",
        }.get(content_hint or "", "memoria")

        return f"""Evalúa la calidad de este {tipo}. Responde SOLO con JSON válido.
KEY: {key}
VALUE: {safe_value}
{{"quality_score": 0-100, "is_valid": true/false, "suggested_fix": "...", "reason": "..."}}"""

    def _parse_critique_response(self, raw: str) -> dict:
        data = parse_json_from_llm(raw)
        if data:
            return data
        # Fallback regex for individual fields
        data = {}
        score_match = re.search(r'"quality_score"\s*:\s*([\d.]+)', raw)
        if score_match:
            data["quality_score"] = float(score_match.group(1))
        valid_match = re.search(r'"is_valid"\s*:\s*(true|false)', raw, re.IGNORECASE)
        if valid_match:
            data["is_valid"] = valid_match.group(1).lower() == "true"
        return data

    # ==================== SELF-HEALING ====================
    async def _detect_duplicates(self) -> int:
        """Find and merge near-duplicate documents (FAISS similarity > 0.92).

        Returns number of duplicates removed.
        """
        removed = 0
        with self._lock:
            docs = list(self.documents)

        if len(docs) < 2:
            return 0

        seen: set[int] = set()
        for i, (key_a, val_a) in enumerate(docs):
            if i in seen:
                continue
            if key_a in self._PROTECTED_EXACT or any(
                key_a.startswith(p) for p in self._PROTECTED_PREFIXES
            ):
                continue
            try:
                emb_a = self._embed(str(val_a))
                if emb_a is None:
                    continue
                distances, indices = self.index.search(
                    emb_a.reshape(1, -1), min(5, self.index.ntotal)
                )
            except Exception:
                logger.warning(
                    "Unhandled exception in MemoryManager._detect_duplicates", exc_info=True
                )
                continue

            for dist, idx in zip(distances[0], indices[0], strict=False):
                if idx < 0 or idx >= len(docs) or idx == i or idx in seen:
                    continue
                similarity = 1.0 / (1.0 + float(dist))
                if similarity > 0.92:
                    key_b, val_b = docs[idx]
                    # Keep the document with higher quality score
                    crit_a = await self._llm_critique(key_a, val_a)
                    crit_b = await self._llm_critique(key_b, val_b)
                    score_a = crit_a.get("quality_score", 0)
                    score_b = crit_b.get("quality_score", 0)

                    if score_a >= score_b:
                        to_remove = (idx, key_b)
                        logger.info(
                            f"Duplicate merged: '{key_b}' → '{key_a}' (sim={similarity:.3f})"
                        )
                    else:
                        to_remove = (i, key_a)
                        logger.info(
                            f"Duplicate merged: '{key_a}' → '{key_b}' (sim={similarity:.3f})"
                        )

                    seen.add(to_remove[0])
                    with self._lock:
                        ws_dir = self.base_dir / self.active_workspace
                        file = ws_dir / f"{to_remove[1]}.md"
                        if file.exists():
                            file.unlink()
                        self.documents = [d for d in self.documents if d[0] != to_remove[1]]
                        self._access_log.pop(to_remove[1], None)
                    removed += 1
                    break  # Only remove one duplicate per source document

        if removed > 0:
            await self._rebuild_index()
        return removed

    async def _resolve_contradictions(self) -> int:
        """Detect contradictory document pairs and ask LLM to resolve.

        Returns number of contradictions resolved.
        """
        resolved = 0
        with self._lock:
            docs = list(self.documents)

        if len(docs) < 2:
            return 0

        # Find similar-but-not-identical pairs (similarity 0.65-0.92)
        checked: set[tuple[int, int]] = set()
        for i, (key_a, val_a) in enumerate(docs):
            if key_a in self._PROTECTED_EXACT or any(
                key_a.startswith(p) for p in self._PROTECTED_PREFIXES
            ):
                continue
            try:
                emb_a = self._embed(str(val_a))
                if emb_a is None:
                    continue
                distances, indices = self.index.search(
                    emb_a.reshape(1, -1), min(3, self.index.ntotal)
                )
            except Exception:
                logger.warning(
                    "Unhandled exception in MemoryManager._resolve_contradictions", exc_info=True
                )
                continue

            for dist, idx in zip(distances[0], indices[0], strict=False):
                if idx < 0 or idx >= len(docs) or idx == i:
                    continue
                pair = tuple(sorted([i, idx]))
                if pair in checked:
                    continue
                checked.add(pair)

                similarity = 1.0 / (1.0 + float(dist))
                if not (0.65 <= similarity <= 0.92):
                    continue

                key_b, val_b = docs[idx]
                resolution = await self._arbitrate_contradiction(key_a, val_a, key_b, val_b)
                if resolution is None:
                    continue

                resolved += 1
                # Write merged resolution, remove original pair
                await self.write(
                    f"merged_{key_a}_{key_b}"[:80],
                    resolution,
                    validated=True,
                )
                with self._lock:
                    ws_dir = self.base_dir / self.active_workspace
                    for rm_key in (key_a, key_b):
                        file = ws_dir / f"{rm_key}.md"
                        if file.exists():
                            file.unlink()
                        self.documents = [d for d in self.documents if d[0] != rm_key]
                        self._access_log.pop(rm_key, None)

        if resolved > 0:
            await self._rebuild_index()
        return resolved

    async def _arbitrate_contradiction(
        self, key_a: str, val_a: Any, key_b: str, val_b: Any
    ) -> str | None:
        """Ask LLM to reconcile two potentially contradictory facts."""
        prompt = (
            "You are a memory consolidation system. Two stored facts may contradict.\n"
            f"Fact A ({key_a}): {str(val_a)[:500]}\n"
            f"Fact B ({key_b}): {str(val_b)[:500]}\n\n"
            "If they DON'T contradict, reply with the single word: SKIP\n"
            "If they DO contradict or overlap, produce a SINGLE consolidated fact "
            "that resolves the conflict. Keep the consolidated fact under 300 characters. "
            "Reply with just the consolidated text, no quotes, no JSON."
        )
        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="critique",
                temperature=0.0,
            )
            text = clean_llm_response(response).strip()
            if text.upper().startswith("SKIP"):
                return None
            if len(text) > 10:
                logger.info(f"Contradiction resolved: '{key_a}' + '{key_b}' → merged")
                return text
        except Exception as e:
            logger.warning(f"Contradiction arbitration failed: {e}")
        return None

    async def _prune_stale(self, max_age_days: int = 30) -> int:
        """Remove documents not accessed in max_age_days (skipping protected keys)."""
        threshold = time.time() - (max_age_days * 86400)
        removed = 0

        with self._lock:
            stale_keys = []
            for key, _val in self.documents:
                if key in self._PROTECTED_EXACT or any(
                    key.startswith(p) for p in self._PROTECTED_PREFIXES
                ):
                    continue
                last_access = self._access_log.get(key, 0)
                if last_access < threshold:
                    stale_keys.append(key)

            if stale_keys:
                ws_dir = self.base_dir / self.active_workspace
                for key in stale_keys:
                    file = ws_dir / f"{key}.md"
                    if file.exists():
                        file.unlink()
                    self._access_log.pop(key, None)
                    logger.info(f"Pruned stale document: {key}")
                self.documents = [d for d in self.documents if d[0] not in stale_keys]
                removed = len(stale_keys)

        if removed > 0:
            await self._rebuild_index()
        return removed

    async def _rebuild_index(self) -> None:
        """Rebuild FAISS index from current documents (called after batch modifications)."""
        with self._lock:
            doc_snapshot = list(self.documents)

        precomputed: list = []
        for _, val in doc_snapshot:
            try:
                emb = self._embed(str(val))
                if emb is not None:
                    precomputed.append(emb)
            except Exception as e:
                logger.warning(f"Error generating embedding during index rebuild: {e}")

        with self._lock:
            self.index = faiss.IndexFlatL2(FAISS_DIMENSION)
            for emb in precomputed:
                self.index.add(emb.reshape(1, -1))
            logger.debug(f"FAISS index rebuilt: {self.index.ntotal} vectors")

    async def self_healing_check(self):
        if self.active_workspace is None:
            logger.info("Self-healing cancelado: sin workspace activo")
            return

        logger.info(f"🔧 Iniciando self-healing en workspace '{self.active_workspace}'...")

        with self._lock:
            documents_to_check = list(self.documents)[-20:]

        low_quality = []
        for key, value in documents_to_check:
            if key in self._PROTECTED_EXACT or any(
                key.startswith(p) for p in self._PROTECTED_PREFIXES
            ):
                continue
            critique = await self._llm_critique(key, value)
            if critique.get("quality_score", 0) < 60:
                low_quality.append((key, critique))
                logger.warning(
                    f"📉 Baja calidad detectada: {key} (score: {critique.get('quality_score')})"
                )

        for key, critique in low_quality:
            if critique.get("suggested_fix"):
                logger.info(f"🔧 Aplicando auto-corrección a {key}")
                await self.write(key, critique["suggested_fix"], validated=True)
            else:
                with self._lock:
                    ws_dir = self.base_dir / self.active_workspace
                    file = ws_dir / f"{key}.md"
                    if file.exists():
                        file.unlink()
                    self.documents = [doc for doc in self.documents if doc[0] != key]
                    self._access_log.pop(key, None)
                    logger.warning(f"🗑️ Eliminado por baja calidad: {key}")

        # Phase 2: Duplicate detection via FAISS similarity
        dup_count = await self._detect_duplicates()

        # Phase 3: Contradiction resolution via LLM arbitration
        contra_count = await self._resolve_contradictions()

        # Phase 4: Prune stale documents (30+ days unaccessed)
        pruned_count = await self._prune_stale(max_age_days=30)

        # Atomic index rebuild: take snapshot under lock,
        # precompute embeddings outside the lock, then rebuild
        # the index inside the lock with the precomputed embeddings.
        with self._lock:
            doc_snapshot = list(self.documents)

        # Precalcular embeddings FUERA del lock
        precomputed: list = []
        for _, val in doc_snapshot:
            try:
                emb = self._embed(str(val))
                if emb is not None:
                    precomputed.append(emb)
            except Exception as e:
                logger.warning(f"Error generando embedding durante self-healing: {e}")

        # Rebuild index inside the lock
        with self._lock:
            self.index = faiss.IndexFlatL2(FAISS_DIMENSION)
            for emb in precomputed:
                self.index.add(emb.reshape(1, -1))

        logger.info(
            f"✅ Self-healing completado en workspace '{self.active_workspace}' | "
            f"Revisados: {len(documents_to_check)} | Baja calidad: {len(low_quality)} | "
            f"Duplicados: {dup_count} | Contradicciones: {contra_count} | Poda: {pruned_count}"
        )

    # ==================== PUBLIC METHODS ====================
    def search(self, query: str, k: int = 5, min_similarity: float = 0.0) -> list[dict]:
        """Búsqueda semántica real usando FAISS. Retorna top-k documentos con scores."""
        query_emb = self._embed(query)
        if query_emb is None:
            return []
        with self._lock:
            if self.index is None or self.index.ntotal == 0:
                return []
            try:
                distances, indices = self.index.search(
                    query_emb.reshape(1, -1), min(k, self.index.ntotal)
                )
                results = []
                for dist, idx in zip(distances[0], indices[0], strict=False):
                    if idx < 0 or idx >= len(self.documents):
                        continue
                    similarity_score = 1.0 / (1.0 + dist)
                    if similarity_score < min_similarity and min_similarity > 0:
                        continue
                    key, val = self.documents[idx]
                    self._access_log[key] = time.time()
                    results.append(
                        {
                            "key": key,
                            "value": val,
                            "distance": float(dist),
                            "similarity": round(1.0 / (1.0 + float(dist)), 4),
                        }
                    )
                return results
            except Exception as e:
                logger.error(f"Error en búsqueda semántica: {e}")
                return []

    def read(self, key: str) -> Any:
        with self._lock:
            for k, v in self.documents:
                if k == key:
                    self._access_log[key] = time.time()
                    return v
            return None

    def get_user_profile(self) -> dict:
        profile = self.read("user_profile")
        return (
            profile
            if isinstance(profile, dict)
            else {"name": None, "country": None, "preferences": {}}
        )

    async def update_user_profile(self, new_data: dict) -> bool:
        if not new_data:
            return False
        current = self.get_user_profile()
        updated = {**current, **new_data}
        return await self.write("user_profile", updated, validated=True)


# Instancia global
memory = MemoryManager()
