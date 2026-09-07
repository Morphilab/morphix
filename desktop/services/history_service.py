import datetime
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_async_session
from core.embedding_provider import EmbeddingProvider
from core.models import Conversation, Message
from core.repositories.conversation_repository import ConversationRepository

logger = logging.getLogger(__name__)

_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = EmbeddingProvider.get_instance()
    return _embed_model


class HistoryService:
    # umbral sobre vectores NORMALIZADOS (L2² = 2 − 2·cos):
    # dist < 0.9 ⇔ coseno > ~0.60 — recall orientado a conversaciones
    # relacionadas. El 0.4 heredado asumía escala no normalizada y dejaba
    # la búsqueda semántica casi siempre vacía.
    _SEMANTIC_DIST_THRESHOLD = 0.9

    @staticmethod
    async def _keyword_fallback(query: str, session: AsyncSession) -> list[Conversation]:
        """Búsqueda SQL LIKE por contenido de mensajes cuando el
        modelo de embeddings no está disponible."""
        like = f"%{query[:60]}%"
        stmt = (
            select(Conversation)
            .join(
                Message,
                Message.conversation_id == Conversation.id,  # type: ignore[arg-type,attr-defined]
            )
            .where(Message.content.ilike(like))  # type: ignore[attr-defined,union-attr]
            .distinct()
            .order_by(Conversation.created_at.desc())  # type: ignore[attr-defined]
            .limit(20)
        )
        result = await session.execute(stmt)
        convs = result.scalars().all()
        seen: set = set()
        out: list[Conversation] = []
        for c in convs:
            if c.id not in seen:
                seen.add(c.id)
                out.append(c)
        return out

    @staticmethod
    async def load_conversations(query: str = "") -> list[Conversation]:
        async with get_async_session() as session:
            stmt = select(Conversation).order_by(Conversation.created_at.desc())  # type: ignore[attr-defined]

            if query:
                keyword = query.lower().strip()
                date_match = re.search(r"date:(\d{4}-\d{2}-\d{2})", keyword)
                tag_match = re.search(r"tag:(\w+)", keyword)

                if date_match:
                    target_date = datetime.datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
                    stmt = stmt.where(Conversation.created_at.cast(datetime.date) == target_date)  # type: ignore[attr-defined]
                elif tag_match:
                    stmt = stmt.where(
                        Conversation.tags.ilike(  # type: ignore[union-attr]
                            func.concat("%", tag_match.group(1), "%")
                        )
                    )
                else:
                    keyword_escaped = keyword.replace("%", "\\%").replace("_", "\\_")
                    stmt = stmt.where(
                        Conversation.title.ilike(  # type: ignore[attr-defined]
                            func.concat("%", keyword_escaped, "%")
                        )
                        | Conversation.tags.ilike(  # type: ignore[union-attr]
                            func.concat("%", keyword_escaped, "%")
                        )
                    )

            result = await session.execute(stmt)
            conversations = result.scalars().all()

            if not conversations and query and not date_match and not tag_match:
                try:
                    conversations = await HistoryService.semantic_search(query, session)
                    conversations.sort(key=lambda c: c.created_at, reverse=True)
                except Exception:
                    # la carga de historial NUNCA debe romperse por RAG
                    logger.warning("semantic_search falló — se ignora", exc_info=True)
                    conversations = []

            return conversations

    @staticmethod
    async def semantic_search(query: str, session: AsyncSession) -> list[Conversation]:
        """Búsqueda semántica con FAISS sobre la columna Message.embedding.

        La columna persistida ES la caché: los mensajes sin embedding se
        encodean en batch UNA vez (backfill lazy), se guardan como float32 LE
        y el flush lo confirma (el commit final lo hace get_async_session).
        Sin re-encodeo por query ni índice Redis de vectores desechable.
        """
        import asyncio

        embed_model = _get_embed_model()
        if embed_model is None:
            # provider zombi/no listo → fallback keyword, no crash
            logger.warning("Embeddings no disponibles — fallback keyword en semantic_search")
            return await HistoryService._keyword_fallback(query, session)
        try:
            # encode de CLASE EmbeddingProvider — aplica prefijo de la
            # familia (e5: 'query: ') y normalización L2 para escala de umbrales.
            query_emb = await asyncio.to_thread(EmbeddingProvider.encode, query, "query")
        except Exception:
            logger.warning("Encode falló — fallback keyword", exc_info=True)
            return await HistoryService._keyword_fallback(query, session)
        if query_emb is None:
            # el encode retorna None cuando el modelo no está listo
            return await HistoryService._keyword_fallback(query, session)

        stmt = select(Message).order_by(Message.id.desc()).limit(200)  # type: ignore[union-attr]
        result = await session.execute(stmt)
        all_msgs = result.scalars().all()

        if not all_msgs:
            return []

        import numpy as np

        # backfill lazy — mensajes sin embedding O con fingerprint de
        # otra familia (misma-dim de otro modelo mezclaría escalas).
        current_fp = EmbeddingProvider.fingerprint()
        missing = [m for m in all_msgs if not m.embedding or m.embedding_fp != current_fp]
        if missing:
            texts = [m.content for m in missing]
            embs = await asyncio.to_thread(EmbeddingProvider.encode_batch, texts)
            for m, emb in zip(missing, embs, strict=False):
                if emb is not None:
                    m.embedding = np.asarray(emb, dtype=np.float32).tobytes()
                    m.embedding_fp = current_fp
            await session.flush()  # commit final lo hace get_async_session

        # infraestructura compartida FAISSIndexer en vez de faiss crudo
        from core.faiss_indexer import FAISSIndexer

        dim = int(np.asarray(query_emb).shape[-1])
        ix = FAISSIndexer(dimension=dim, embedder=EmbeddingProvider)

        conv_by_msg: dict[str, int | None] = {}
        added = 0
        for msg in all_msgs:
            if not msg.embedding or msg.embedding_fp != current_fp:
                continue  # sin embedding o de otra familia — ignorar, no romper RAG
            emb = np.frombuffer(msg.embedding, dtype=np.float32)
            try:
                ix.add_embedding(str(msg.id), msg.content, emb)
            except ValueError:
                continue  # vector de otra escala/dim — ignorar, no romper RAG
            conv_by_msg[str(msg.id)] = msg.conversation_id
            added += 1

        if not added:
            return []

        hits = ix.search(query, k=10)

        matched_ids = [
            cid
            for h in hits
            if h.get("distance", float("inf")) < HistoryService._SEMANTIC_DIST_THRESHOLD
            and (cid := conv_by_msg.get(str(h.get("key")))) is not None
        ]

        if not matched_ids:
            return []

        stmt2 = select(Conversation).where(Conversation.id.in_(matched_ids))  # type: ignore[union-attr]
        result2 = await session.execute(stmt2)
        return result2.scalars().all()  # type: ignore[return-value]

    @staticmethod
    async def delete_conversation(conv_id: int) -> bool:
        return await ConversationRepository.delete(conv_id)

    @staticmethod
    async def get_messages(conv_id: int) -> list[dict]:
        """Obtiene todos los mensajes de una conversación."""
        return await ConversationRepository.get_messages(conv_id)

    @staticmethod
    async def get_conversation(conv_id: int) -> dict | None:
        """Get conversation metadata with message count."""
        return await ConversationRepository.get_conversation(conv_id)

    @staticmethod
    async def list_conversations(query: str = "", limit: int = 50) -> list[dict]:
        """Lista de conversaciones (nuevas primero), dicts para la GUI.

        Sin ``query``: passthrough plano a ``list_all`` (comportamiento
        histórico). Con ``query``: delega en ``load_conversations`` — filtros
        ``date:YYYY-MM-DD`` / ``tag:x`` / texto (title/tags) con fallback
        semántico — y normaliza a dict (es la ruta viva del buscador del
        Historial).
        """
        if not query:
            return await ConversationRepository.list_all(limit=limit, offset=0)
        convs = await HistoryService.load_conversations(query)
        return [
            {
                "id": c.id,
                "title": c.title,
                "created_at": getattr(c, "created_at", None),
                "tags": getattr(c, "tags", ""),
            }
            for c in convs
        ][:limit]

    @staticmethod
    async def export_conversation(conv_id: int, format: str = "md"):
        return await ConversationRepository.export(conv_id, format)
