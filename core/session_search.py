# core/session_search.py — búsqueda textual de sesiones + estado derivado
"""Capa textual sobre las conversaciones de un workspace.

Capa textual sobre PostgreSQL async, sin duplicar la memoria vectorial
existente (``core/memory/manager.py`` + faiss cubren lo semántico; aquí va
lo lexical — FTS y el estado de sesión eran el hueco).

Contratos:

- **FTS primero, LIKE de respaldo**: ``to_tsvector('simple')`` con
  ``plainto_tsquery`` + ``ts_rank``/``ts_headline``; si no hay filas (o la
  consulta trae CJK, que 'simple' tokeniza mal), se cae a ``ILIKE`` con
  wildcards neutralizados (``_strip_cjk_wildcards``).
- **query_rewrite pre-retrieval**: normalización determinista — minúsculas,
  ruido de puntuación fuera, CJK a bigramas, sin duplicados, tope de
  términos — antes de tocar la BD.
- **Estado de sesión derivado** (``classify_session_status``):
  nada de columna nueva — el estado se calcula de (último mensaje, nº de
  mensajes, pausas sin resolver) en el momento de listar.

Las consultas son FUNCIONALES (sin índice GIN): correctas a cualquier
escala de desarrollo; si el volumen lo pide, el índice expresión GIN se
añade por delta estilo ``a15xvi`` — no se toca la baseline.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import TextClause, text

from core.database import bound_schema, get_async_session

MAX_TERMS = 12
MAX_QUERY_CHARS = 256
SNIPPET_MAX_CHARS = 240

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_NOISE_RE = re.compile(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_LIKE_WILDCARDS_RE = re.compile(r"[%_\\]")

# Estados derivados (adaptados al modelo Morphix):
# el Message de Morphix no persiste tool_calls/finish_reason, así que el
# clasificador se apoya en (último rol, nº de mensajes, pausa sin resolver).
STATUS_EMPTY = "empty"
STATUS_PAUSED = "paused"
STATUS_AWAITING_AGENT = "awaiting_agent"
STATUS_AWAITING_USER = "awaiting_user"
STATUS_UNKNOWN = "unknown"


# ── query_rewrite (pre-retrieval, puro) ─────────────────────────────────────


def rewrite_query(query: str) -> str:
    """Normaliza la consulta para FTS/LIKE: determinista y sin ruido.

    - minúsculas y puntuación fuera (excepto CJK);
    - runs CJK → bigramas (la config 'simple' no segmenta CJK: el bigrama
      + el respaldo ILIKE recuperan las frases);
    - duplicados fuera, tope de términos, tope de longitud total.
    """
    raw = (query or "").strip().lower()[:MAX_QUERY_CHARS]
    if not raw:
        return ""
    tokens: list[str] = []
    for chunk in _NOISE_RE.split(raw):
        if not chunk:
            continue
        if _CJK_RE.search(chunk):
            tokens.extend(_cjk_bigrams(chunk))
        else:
            tokens.append(chunk)
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return " ".join(unique[:MAX_TERMS])


def _cjk_bigrams(chunk: str) -> list[str]:
    if len(chunk) <= 2:
        return [chunk]
    return [chunk[i : i + 2] for i in range(len(chunk) - 1)]


def has_cjk(query: str) -> bool:
    return bool(_CJK_RE.search(query or ""))


def _like_pattern(query: str) -> str:
    """Patrón ILIKE con wildcards del usuario neutralizados."""
    cleaned = _LIKE_WILDCARDS_RE.sub(" ", query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return f"%{cleaned}%"


# ── Estado de sesión derivado (puro) ────────────────────────────────────────


def classify_session_status(
    last_role: str | None, message_count: int, paused_unresolved: bool
) -> str:
    """Estado de una conversación a partir de lo que el modelo persiste.

    - paused: hay una PausedSession sin resolver (gana a todo).
    - empty: sin mensajes.
    - awaiting_agent: el último mensaje es del usuario → turno pendiente.
    - awaiting_user: el último es del asistente → esperando al usuario.
    - unknown: rol no reconocido (herramientas internas, etc.).
    """
    if paused_unresolved:
        return STATUS_PAUSED
    if message_count <= 0 or last_role is None:
        return STATUS_EMPTY
    role = str(last_role).lower()
    if role == "user":
        return STATUS_AWAITING_AGENT
    if role in {"assistant", "system", "tool"}:
        return STATUS_AWAITING_USER
    return STATUS_UNKNOWN


# ── Búsqueda textual (PG) ──────────────────────────────────────────────────


def _fts_sql(limit: int, filtered: bool) -> TextClause:
    conv_filter = "AND m.conversation_id = :conv_id" if filtered else ""
    return text(
        f"""
        SELECT m.id, m.conversation_id, m.role, m.timestamp,
               ts_rank(to_tsvector('simple', m.content),
                       plainto_tsquery('simple', :tsq)) AS rank,
               ts_headline('simple', m.content, plainto_tsquery('simple', :tsq),
                           'MaxFragments=2, MinWords=5, MaxWords=30, '
                           'StartSel=[, StopSel=]') AS snippet
        FROM message m
        WHERE to_tsvector('simple', m.content) @@ plainto_tsquery('simple', :tsq)
          {conv_filter}
        ORDER BY rank DESC, m.timestamp DESC
        LIMIT :limit
        """
    ).bindparams(limit=limit)


def _like_sql(limit: int, filtered: bool) -> TextClause:
    conv_filter = "AND m.conversation_id = :conv_id" if filtered else ""
    return text(
        f"""
        SELECT m.id, m.conversation_id, m.role, m.timestamp,
               0.0 AS rank,
               left(m.content, {SNIPPET_MAX_CHARS}) AS snippet
        FROM message m
        WHERE m.content ILIKE :pattern
          {conv_filter}
        ORDER BY m.timestamp DESC
        LIMIT :limit
        """
    ).bindparams(limit=limit)


async def search_messages(
    workspace: str,
    query: str,
    *,
    conversation_id: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Búsqueda lexical sobre los mensajes del workspace.

    FTS ('simple') primero; respaldo ILIKE cuando FTS no arroja filas o la
    consulta contiene CJK. Devuelve filas con id/conversation_id/role/
    timestamp/rank/snippet, mejor rank primero.
    """
    rewritten = rewrite_query(query)
    if not rewritten:
        return []
    limit = max(1, min(int(limit), 100))
    async with bound_schema(workspace):
        async with get_async_session() as session:
            if has_cjk(query):
                return await _run_like(session, query, conversation_id, limit)
            rows = await _run_fts(session, rewritten, conversation_id, limit)
            if rows:
                return rows
            return await _run_like(session, query, conversation_id, limit)


async def _run_fts(session, tsq: str, conv_id: int | None, limit: int) -> list[dict]:
    stmt = _fts_sql(limit, filtered=conv_id is not None)
    params: dict[str, Any] = {"tsq": tsq}
    if conv_id is not None:
        params["conv_id"] = conv_id
    result = await session.execute(stmt, params)
    return [dict(row._mapping) for row in result]


async def _run_like(session, query: str, conv_id: int | None, limit: int) -> list[dict]:
    stmt = _like_sql(limit, filtered=conv_id is not None)
    params: dict[str, Any] = {"pattern": _like_pattern(query)}
    if conv_id is not None:
        params["conv_id"] = conv_id
    result = await session.execute(stmt, params)
    return [dict(row._mapping) for row in result]


async def conversation_statuses(
    workspace: str, conversation_ids: list[int] | None = None
) -> dict[int, dict[str, Any]]:
    """Estado derivado por conversación (para listados/UI).

    Devuelve {id: {"status", "last_role", "message_count", "paused"}}.
    Sin escribir nada: pura lectura de (último mensaje, conteo, pausas).
    """
    ids_filter = ""
    params: dict[str, Any] = {}
    if conversation_ids is not None:
        ids_filter = "WHERE m.conversation_id = ANY(:ids)"
        params["ids"] = list(conversation_ids)

    last_sql = text(
        f"""
        SELECT DISTINCT ON (m.conversation_id)
               m.conversation_id, m.role
        FROM message m
        {ids_filter}
        ORDER BY m.conversation_id, m.timestamp DESC, m.id DESC
        """
    )
    count_sql = text(
        f"""
        SELECT m.conversation_id, COUNT(*) AS n
        FROM message m
        {ids_filter}
        GROUP BY m.conversation_id
        """
    )
    paused_sql = text(
        """
        SELECT DISTINCT p.conversation_id
        FROM paused_sessions p
        WHERE p.resolved_at IS NULL AND p.conversation_id IS NOT NULL
        """
    )

    async with bound_schema(workspace):
        async with get_async_session() as session:
            last = {int(r.conversation_id): r.role for r in await session.execute(last_sql, params)}
            counts = {
                int(r.conversation_id): int(r.n) for r in await session.execute(count_sql, params)
            }
            paused_rows = await session.execute(paused_sql)
            paused = {int(r.conversation_id) for r in paused_rows}

    out: dict[int, dict[str, Any]] = {}
    for conv_id in set(last) | set(counts):
        out[conv_id] = {
            "status": classify_session_status(
                last.get(conv_id), counts.get(conv_id, 0), conv_id in paused
            ),
            "last_role": last.get(conv_id),
            "message_count": counts.get(conv_id, 0),
            "paused": conv_id in paused,
        }
    return out
