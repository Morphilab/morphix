"""
MemoryManager - Sistema de 3 Capas Self-Healing (VERSIÓN FINAL ROBUSTA Y ESTABLE)
Aislamiento por workspace: subdirectorios memory/{workspace}/
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from core.embedding_provider import EmbeddingProvider
from core.faiss_indexer import FAISS_DIMENSION
from core.utils import clean_llm_response  # noqa: F401 — re-exported for backward compat
from llm import models, parse_json_from_llm

logger = logging.getLogger(__name__)


def _knowledge_entries(workspace: str) -> list[tuple[str, Any]]:
    """PKB: docs de workspaces/<ws>/knowledge/**/*.md → claves 'kb_<ruta>'.

    Se cargan en el switch de workspace (junto a memory/<ws>/*.md) para que
    la búsqueda semántica del MemoryManager cubra el conocimiento curado del
    proyecto SIN subsistema nuevo. El prefijo kb_ es protegido (nunca se
    pisa ni se borra por la tool memory_inspector).
    """
    from core.path_resolver import paths

    entries: list[tuple[str, Any]] = []
    kdir = paths.workspace_knowledge_dir(workspace)
    if not kdir.is_dir():
        return entries
    for file in sorted(kdir.rglob("*.md")):
        rel = file.relative_to(kdir).as_posix()
        key = "kb_" + re.sub(r"[^a-z0-9_]+", "_", rel[: -len(".md")].lower())
        entries.append((key, file.read_text(encoding="utf-8", errors="replace").strip()))
    return entries


class MemoryManager:
    _PROTECTED_EXACT: set[str] = {
        "kairos_daemon_heartbeat",
        "user_profile",
        "user_profile_last_update",
        "last_task_summary",
        "security_private",
        "last_creative_output",
        "last_analysis",
        "last_plan",
        "last_connection",
        "last_successful_code",
    }
    _PROTECTED_PREFIXES: tuple[str, ...] = ("workflow_subtask_", "last_", "merged_", "kb_")

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
        self.index = self._new_index()
        # mapeo estable id→clave (IndexIDMap2) — evita desincronización
        # posicional entre índice y documents en overwrites/rebuilds.
        self._ids: dict[str, int] = {}
        self._id_to_key: dict[int, str] = {}
        self._next_id = 0
        self.embedder = EmbeddingProvider  # lazy — carga en background
        self._access_log: dict[str, float] = {}  # key -> last access timestamp
        # claves excluidas del índice en el último
        # switch por fallo de embedding (consultable; el warning va al log).
        self.last_switch_skipped: list[str] = []
        logger.info("✅ Memoria 3-capas inicializada (embeddings en carga lazy)")

    @staticmethod
    def _new_index():
        """Índice con mapeo estable id→vector: remove_ids y add_with_ids."""
        return faiss.IndexIDMap2(faiss.IndexFlatL2(FAISS_DIMENSION))

    def _remove_entry_locked(self, key: str) -> None:
        """Elimina clave de índice, mapas de id, documentos y access-log. Requiere _lock."""
        vid = self._ids.pop(key, None)
        if vid is not None:
            try:
                self.index.remove_ids(np.array([vid], dtype="int64"))
            except Exception:
                logger.warning("remove_ids falló para '%s'", key, exc_info=True)
            self._id_to_key.pop(vid, None)
        self.documents = [d for d in self.documents if d[0] != key]
        self._access_log.pop(key, None)

    def _embed(self, text: str, kind: str = "passage"):
        """Wrapper que espera al modelo si aún no está listo (kind e5)."""
        if not self.embedder.wait_until_ready(timeout=60):
            logger.warning("Modelo de embeddings no disponible tras timeout")
            return None
        return self.embedder.encode(text, kind=kind)

    async def _embed_async(self, text: str, kind: str = "passage"):
        """Async wrapper — offloads CPU-bound embedding to a thread."""
        import asyncio

        if not self.embedder.wait_until_ready(timeout=60):
            logger.warning("Modelo de embeddings no disponible tras timeout")
            return None
        return await asyncio.to_thread(self.embedder.encode, text, kind=kind)

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
        # hash() de str varía POR PROCESO — sha1 determinista cross-restart
        key = f"correction_{hashlib.sha1(original_task.encode()).hexdigest()[:8]}"
        value = {
            "original": original_task[:300],
            "correction": correction[:800],
            "timestamp": int(time.time()),
        }
        return await self.write(key, value, validated=True, content_hint="analytical")

    # ==================== CAMBIO DE WORKSPACE ====================
    @staticmethod
    def _migrate_legacy_last_update(ws_dir: Path) -> None:
        """Renombra el resumen de última tarea al nombre honesto: la clave
        legacy sugería perfil de usuario pero contenía el resumen del run."""
        legacy = ws_dir / "user_profile_last_update.md"
        if legacy.exists():
            try:
                legacy.replace(ws_dir / "last_task_summary.md")
            except OSError:
                logger.warning("No se pudo renombrar el resumen legacy de tarea", exc_info=True)

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
                self._migrate_legacy_last_update(ws_dir)

                # Read files under lock (fast I/O)
                file_entries: list[tuple[str, Any]] = []
                new_access: dict[str, float] = {}
                for file in ws_dir.glob("*.md"):
                    # legacy write-only — excluir del load (no re-embeddear)
                    if file.stem.startswith("workflow_subtask_"):
                        continue
                    with open(file, encoding="utf-8") as f:
                        content = f.read().strip()
                    key = file.stem
                    val = json.loads(content) if content.startswith("{") else content
                    file_entries.append((key, val))
                    # mtime real como registro de acceso inicial del workspace
                    new_access[key] = file.stat().st_mtime

                # PKB: docs de knowledge/**/*.md con prefijo kb_
                for kb_key, kb_val in _knowledge_entries(workspace):
                    file_entries.append((kb_key, kb_val))
                    new_access[kb_key] = time.time()
            except Exception as e:
                logger.warning("Unhandled exception in MemoryManager", exc_info=True)
                self.active_workspace = old_ws
                self.documents = old_docs
                self.index = old_index
                raise RuntimeError(f"Error switching to workspace '{workspace}': {e}")

        # Compute embeddings in thread pool (slow operation, non-blocking for async)
        def _build_index():
            new_index = MemoryManager._new_index()
            new_docs: list[tuple[str, Any]] = []
            ids: dict[str, int] = {}
            id_to_key: dict[int, str] = {}
            vid = 0
            skipped: list[str] = []  # exclusiones VISIBLES, no silenciosas
            for key, val in file_entries:
                try:
                    emb = self._embed(str(val))
                    if emb is None:
                        raise ValueError("embedding no disponible")
                    new_index.add_with_ids(emb.reshape(1, -1), np.array([vid], dtype="int64"))
                    ids[key] = vid
                    id_to_key[vid] = key
                    vid += 1
                    new_docs.append((key, val))
                except Exception as e:
                    # lo que falla embedding se excluye de AMBAS estructuras
                    # (ntotal == len(documents)) — pero se registra y se
                    # reporta en el resumen del switch.
                    skipped.append(key)
                    logger.warning(
                        "embedding falló para '%s' (%s) — excluido del índice semántico",
                        key,
                        e,
                    )
            return new_index, new_docs, ids, id_to_key, vid, skipped

        new_index, new_docs, ids, id_to_key, next_vid, skipped = await asyncio.to_thread(
            _build_index
        )

        # Atomic swap of index and documents under lock
        with self._lock:
            doc_keys = {k for k, _ in new_docs}
            new_access = {k: v for k, v in new_access.items() if k in doc_keys}
            self.active_workspace = workspace
            self.documents = new_docs
            self.index = new_index
            self._ids = ids
            self._id_to_key = id_to_key
            self._next_id = next_vid
            # access-log limpio y poblado con mtimes reales del workspace entrante
            self._access_log.clear()
            self._access_log.update(new_access)
            # registro consultable de lo excluido en el último switch
            self.last_switch_skipped = skipped
            logger.info(f"🔄 Workspace switched to '{workspace}' ({len(new_docs)} documents)")
        if skipped:
            logger.warning(
                "switch a '%s': %d documento(s) EXCLUIDOS del índice semántico por fallo de "
                "embedding: %s — revisa logs y el estado del embedder",
                workspace,
                len(skipped),
                ", ".join(skipped[:8]) + ("…" if len(skipped) > 8 else ""),
            )

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

    # ==================== ROBUST WRITE WITH ROLLBACK ====================
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
        embedding = await self._embed_async(str(value))
        if embedding is None:
            logger.error(f"❌ Embedding no disponible para '{key}'")
            return False

        needs_rebuild = False
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

                # vector con id estable — overwrite purga el vector viejo
                # vía remove_ids en lugar de añadir un huérfano.
                vid = self._ids.get(key)
                if vid is None:
                    vid = self._next_id
                    self._next_id += 1
                else:
                    self.index.remove_ids(np.array([vid], dtype="int64"))
                    self._id_to_key.pop(vid, None)
                self.index.add_with_ids(embedding.reshape(1, -1), np.array([vid], dtype="int64"))
                self._ids[key] = vid
                self._id_to_key[vid] = key

                self.documents.append((key, value))
                self._access_log[key] = time.time()
            except Exception as e:
                logger.error(f"Error saving '{key}': {e}", exc_info=True)
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
                # el índice pudo quedar modificado a medias — reconstruir alineado.
                needs_rebuild = True
                return False

        if needs_rebuild:
            await self._rebuild_index()
            return False

        logger.info(f"✅ Memoria escrita: {key} (score: {score})")
        return True

    def _get_quality_threshold(self, content_hint: str | None, key: str) -> int:
        if key in ("user_profile_last_update", "last_task_summary"):
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

        seen: set[str] = set()
        for _i, (key_a, val_a) in enumerate(docs):
            if key_a in seen:
                continue
            if self._is_protected_key(key_a):
                continue
            try:
                emb_a = await self._embed_async(str(val_a))
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

            for dist, vid in zip(distances[0], indices[0], strict=False):
                vid = int(vid)
                if vid < 0:
                    continue
                # resolución por id estable — no posicional
                key_b = self._id_to_key.get(vid)
                if key_b is None or key_b == key_a or key_b in seen:
                    continue
                similarity = 1.0 / (1.0 + float(dist))
                if similarity > 0.92:
                    val_b = next((v for k, v in docs if k == key_b), None)
                    if val_b is None:
                        continue
                    # Keep the document with higher quality score
                    crit_a = await self._llm_critique(key_a, val_a)
                    crit_b = await self._llm_critique(key_b, val_b)
                    score_a = crit_a.get("quality_score", 0)
                    score_b = crit_b.get("quality_score", 0)

                    if score_a >= score_b:
                        loser = key_b
                        logger.info(
                            f"Duplicate merged: '{key_b}' → '{key_a}' (sim={similarity:.3f})"
                        )
                    else:
                        loser = key_a
                        logger.info(
                            f"Duplicate merged: '{key_a}' → '{key_b}' (sim={similarity:.3f})"
                        )

                    seen.add(loser)
                    with self._lock:
                        ws_dir = self.base_dir / self.active_workspace
                        file = ws_dir / f"{loser}.md"
                        if file.exists():
                            file.unlink()
                        self._remove_entry_locked(loser)
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
        checked: set[tuple[str, str]] = set()
        for _i, (key_a, val_a) in enumerate(docs):
            if self._is_protected_key(key_a):
                continue
            try:
                emb_a = await self._embed_async(str(val_a))
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

            for dist, vid in zip(distances[0], indices[0], strict=False):
                vid = int(vid)
                if vid < 0:
                    continue
                # resolución por id estable — no posicional
                key_b = self._id_to_key.get(vid)
                if key_b is None or key_b == key_a:
                    continue
                pair: tuple[str, str] = (key_a, key_b) if key_a <= key_b else (key_b, key_a)
                if pair in checked:
                    continue
                checked.add(pair)

                similarity = 1.0 / (1.0 + float(dist))
                if not (0.65 <= similarity <= 0.92):
                    continue

                val_b = next((v for k, v in docs if k == key_b), None)
                if val_b is None:
                    continue
                resolution = await self._arbitrate_contradiction(key_a, val_a, key_b, val_b)
                if resolution is None:
                    continue

                resolved += 1
                # Hecho consolidado con clave determinista cross-restart
                # (hashlib sha1, no hash()) y prefijo fact_ BUSCABLE (no protegido).
                digest = hashlib.sha1(f"{key_a}|{key_b}".encode()).hexdigest()[:8]
                await self.write(
                    f"fact_{key_a}_{key_b}_{digest}"[:80],
                    resolution,
                    validated=True,
                )
                with self._lock:
                    ws_dir = self.base_dir / self.active_workspace
                    for rm_key in (key_a, key_b):
                        file = ws_dir / f"{rm_key}.md"
                        if file.exists():
                            file.unlink()
                        self._remove_entry_locked(rm_key)

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
            ws_dir = self.base_dir / self.active_workspace
            stale_keys = []
            for key, _val in self.documents:
                if self._is_protected_key(key):
                    continue
                last_access = self._access_log.get(key)
                if last_access is None:
                    # fallback al mtime real del archivo en disco
                    f = ws_dir / f"{key}.md"
                    try:
                        last_access = f.stat().st_mtime if f.exists() else 0.0
                    except OSError:
                        last_access = 0.0
                if last_access < threshold:
                    stale_keys.append(key)

            if stale_keys:
                ws_dir = self.base_dir / self.active_workspace
                for key in stale_keys:
                    file = ws_dir / f"{key}.md"
                    if file.exists():
                        file.unlink()
                    self._remove_entry_locked(key)
                    logger.info(f"Pruned stale document: {key}")
                removed = len(stale_keys)

        if removed > 0:
            await self._rebuild_index()
        return removed

    async def _rebuild_index(self) -> None:
        """Rebuild alineado: los docs cuyo embedding falle se excluyen de
        AMBAS estructuras (índice y documents) para preservar el invariante
        ntotal == len(documents). Regenera los mapas id→clave."""
        with self._lock:
            doc_snapshot = list(self.documents)

        aligned: list[tuple[tuple[str, Any], Any]] = []
        for pair in doc_snapshot:
            try:
                emb = await self._embed_async(str(pair[1]))
            except Exception as e:
                logger.warning(f"Error generating embedding during index rebuild: {e}")
                emb = None
            if emb is not None:
                aligned.append((pair, emb))

        new_index = self._new_index()
        ids: dict[str, int] = {}
        id_to_key: dict[int, str] = {}
        with self._lock:
            next_id = self._next_id
            for (key, _val), emb in aligned:
                vid = ids.get(key)
                if vid is None:
                    vid = next_id
                    next_id += 1
                new_index.add_with_ids(emb.reshape(1, -1), np.array([vid], dtype="int64"))
                ids[key] = vid
                id_to_key[vid] = key
            self.index = new_index
            self._ids = ids
            self._id_to_key = id_to_key
            self._next_id = next_id
            self.documents = [pair for pair, _ in aligned]
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
                    self._remove_entry_locked(key)
                    logger.warning(f"🗑️ Eliminado por baja calidad: {key}")

        # Phase 2: Duplicate detection via FAISS similarity
        dup_count = await self._detect_duplicates()

        # Phase 3: Contradiction resolution via LLM arbitration
        contra_count = await self._resolve_contradictions()

        # Phase 4: Prune stale documents (30+ days unaccessed)
        pruned_count = await self._prune_stale(max_age_days=30)

        # Rebuild SOLO si el healing actuó (incondicional re-embeddearía
        # el corpus completo en cada pasada sin cambios).
        acted = bool(low_quality) or dup_count > 0 or contra_count > 0 or pruned_count > 0
        if acted:
            await self._rebuild_index()

        logger.info(
            f"✅ Self-healing completado en workspace '{self.active_workspace}' | "
            f"Revisados: {len(documents_to_check)} | Baja calidad: {len(low_quality)} | "
            f"Duplicados: {dup_count} | Contradicciones: {contra_count} | Poda: {pruned_count}"
        )

    # ==================== PUBLIC METHODS ====================
    def _is_protected_key(self, key: str) -> bool:
        """Claves protegidas: nunca se recuperan en búsquedas (contaminación de contexto)."""
        return key in self._PROTECTED_EXACT or any(
            key.startswith(p) for p in self._PROTECTED_PREFIXES
        )

    @staticmethod
    def _results_from_ids(
        distances,
        indices,
        doc_map: dict[str, Any],
        id_to_key: dict[int, str],
        access_log: dict[str, float],
        is_protected,
        min_similarity: float,
    ) -> list[dict]:
        """Resuelve resultados por id estable (IndexIDMap2), no posicional."""
        results: list[dict] = []
        for dist, vid in zip(distances[0], indices[0], strict=False):
            vid = int(vid)
            if vid < 0:
                continue
            key = id_to_key.get(vid)
            if key is None or is_protected(key):
                continue
            val = doc_map.get(key)
            if val is None:
                continue
            if min_similarity > 0 and 1.0 / (1.0 + dist) < min_similarity:
                continue
            access_log[key] = time.time()
            results.append(
                {
                    "key": key,
                    "value": val,
                    "distance": float(dist),
                    "similarity": round(1.0 / (1.0 + float(dist)), 4),
                }
            )
        return results

    def search(self, query: str, k: int = 5, min_similarity: float = 0.0) -> list[dict]:
        """Búsqueda semántica real usando FAISS. Retorna top-k documentos con scores."""
        query_emb = self._embed(query, kind="query")  # A-XI
        if query_emb is None:
            return []
        with self._lock:
            if self.index is None or self.index.ntotal == 0:
                return []
            try:
                distances, indices = self.index.search(
                    query_emb.reshape(1, -1), min(k, self.index.ntotal)
                )
                doc_map = {k_: v for k_, v in self.documents}
                return self._results_from_ids(
                    distances,
                    indices,
                    doc_map,
                    self._id_to_key,
                    self._access_log,
                    self._is_protected_key,
                    min_similarity,
                )
            except Exception as e:
                logger.error(f"Error en búsqueda semántica: {e}")
                return []

    async def search_async(self, query: str, k: int = 5, min_similarity: float = 0.0) -> list[dict]:
        """Async version — offloads embedding computation to a thread."""
        import asyncio

        query_emb = await self._embed_async(query, kind="query")  # A-XI
        if query_emb is None:
            return []

        def _faiss_search():
            with self._lock:
                if self.index is None or self.index.ntotal == 0:
                    return []
                try:
                    distances, indices = self.index.search(
                        query_emb.reshape(1, -1), min(k, self.index.ntotal)
                    )
                    doc_map = {k_: v for k_, v in self.documents}
                    return self._results_from_ids(
                        distances,
                        indices,
                        doc_map,
                        self._id_to_key,
                        self._access_log,
                        self._is_protected_key,
                        min_similarity,
                    )
                except Exception as e:
                    logger.error(f"Error en búsqueda semántica: {e}")
                    return []

        return await asyncio.to_thread(_faiss_search)

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

    @staticmethod
    def _deep_merge_profile(current: dict, new_data: dict) -> dict:
        """Merge profundo — 'preferences' se fusiona clave a clave en vez
        de reemplazar el dict entero (pérdida silenciosa de preferencias)."""
        merged = {**current}
        for k, v in new_data.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            elif v is not None:
                merged[k] = v
        return merged

    async def update_user_profile(self, new_data: dict) -> bool:
        if not new_data:
            return False
        current = self.get_user_profile()
        current_has_data = any(current.values())
        updated = self._deep_merge_profile(current, new_data)
        from core.utils import is_trivial_profile

        # Gate contextual anti-stale: con perfil YA poblado, hechos
        # solo-nombre no lo tocan (una conversación contaminada no re-escribe
        # el nombre). Con perfil vacío, sembrar con el nombre es el dato
        # legítimo que el usuario acaba de decir — rechazarlo dejaba al
        # sistema sin memoria cross-sesión (user_profile jamás nacía).
        if current_has_data and is_trivial_profile(new_data):
            return False
        return await self.write("user_profile", updated, validated=True)


# Instancia global
memory = MemoryManager()
