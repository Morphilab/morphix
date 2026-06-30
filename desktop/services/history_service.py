import datetime
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_async_session
from core.embedding_provider import EmbeddingProvider
from core.models import Conversation, Message
from core.repositories.conversation_repository import ConversationRepository
from llm import models

logger = logging.getLogger(__name__)

_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = EmbeddingProvider.get_instance()
    return _embed_model


class HistoryService:
    _redis_client = None

    @staticmethod
    async def _get_redis():
        if HistoryService._redis_client is None:
            try:
                from core.config import settings

                redis_url = settings.redis_url
                import redis.asyncio as aioredis

                HistoryService._redis_client = aioredis.from_url(
                    redis_url, socket_connect_timeout=2, socket_timeout=2
                )
                logger.info(f"Redis cache conectado: {redis_url}")
            except Exception as e:
                logger.warning(f"Redis no disponible: {e}")
                HistoryService._redis_client = None
        return HistoryService._redis_client

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
                conversations = await HistoryService.semantic_search(query, session)
                conversations.sort(key=lambda c: c.created_at, reverse=True)

            return conversations

    @staticmethod
    async def semantic_search(query: str, session: AsyncSession) -> list[Conversation]:
        """Búsqueda semántica con FAISS + caché Redis (ejecuta encode en thread)."""
        import asyncio

        embed_model = _get_embed_model()
        query_emb = await asyncio.to_thread(embed_model.encode, query)

        stmt = select(Message).order_by(Message.id.desc()).limit(200)  # type: ignore[union-attr]
        result = await session.execute(stmt)
        all_msgs = result.scalars().all()

        if not all_msgs:
            return []

        import faiss
        import numpy as np

        r = await HistoryService._get_redis()
        embeddings = []
        for msg in all_msgs:
            cache_key = f"emb:{msg.id}"
            cached_emb = None
            if r:
                cached_emb = await r.get(cache_key)
            if cached_emb:
                emb = np.frombuffer(cached_emb, dtype=np.float32)
            else:
                emb = await asyncio.to_thread(embed_model.encode, msg.content)
                if r:
                    await r.set(cache_key, emb.tobytes(), ex=3600)
            embeddings.append(emb)

        embeddings_arr = np.array(embeddings).astype("float32")  # type: ignore[assignment]
        index = faiss.IndexFlatL2(embeddings_arr.shape[1])  # type: ignore[attr-defined]
        index.add(embeddings_arr)

        distances, indices = index.search(np.array([query_emb]).astype("float32"), 10)

        matched_ids = [
            all_msgs[idx].conversation_id
            for idx, dist in zip(indices[0], distances[0], strict=False)
            if dist < 0.4
        ]

        if not matched_ids:
            return []

        stmt2 = select(Conversation).where(Conversation.id.in_(matched_ids))  # type: ignore[union-attr]
        result2 = await session.execute(stmt2)
        return result2.scalars().all()  # type: ignore[return-value]

    @staticmethod
    async def perform_rag_search(query: str, limit: int = 6) -> list[dict]:
        async with get_async_session() as session:
            relevant_convs = await HistoryService.semantic_search(query, session)
            results = []

            for conv in relevant_convs[:limit]:
                stmt = (
                    select(Message)
                    .where(Message.conversation_id == conv.id)  # type: ignore[arg-type]
                    .order_by(Message.timestamp)  # type: ignore[arg-type]
                    .limit(5)
                )
                result = await session.execute(stmt)
                messages = result.scalars().all()

                snippet_parts = []
                for msg in messages:
                    preview = msg.content.strip()
                    if len(preview) > 200:
                        preview = preview[:200] + "..."
                    snippet_parts.append(f"{msg.role.capitalize()}: {preview}")

                snippet = "\n".join(snippet_parts) or "(Sin contenido)"

                results.append(
                    {
                        "title": conv.title or "Sin título",
                        "created_at": conv.created_at,
                        "snippet": snippet,
                        "id": conv.id,
                    }
                )
            return results

    @staticmethod
    async def rag_query(query: str) -> dict:
        if not query.strip():
            return {"success": False, "message": "La pregunta está vacía"}

        results = await HistoryService.perform_rag_search(query)
        if not results:
            return {
                "success": False,
                "message": "No encontré conversaciones relevantes en tu historial.",
            }

        context = "\n\n".join(
            [
                f"Conversación: {r['title']} ({r['created_at'].strftime('%d/%m/%Y %H:%M')})\n"
                f"{r['snippet']}\n{'─' * 40}"
                for r in results
            ]
        )

        prompt = f"""Eres un asistente personal que conoce todo el historial del usuario.

Pregunta del usuario: {query}

Contexto relevante de su historial (más reciente primero):
{context}

Responde de forma natural, útil y conversacional. Usa el contexto para dar respuestas precisas y personales."""

        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="default",
                temperature=0.7,
            )
            answer = response.choices[0].message.content.strip()
            return {"success": True, "answer": answer, "sources": len(results)}
        except Exception as e:
            logging.error(f"Error en RAG query: {e}")
            return {"success": False, "message": f"Error al procesar la pregunta: {e!s}"}

    # === CRUD delegation (all async now) ===
    @staticmethod
    async def edit_conversation(conv_id: int, new_title: str) -> bool:
        return await ConversationRepository.update_title(conv_id, new_title)

    @staticmethod
    async def delete_conversation(conv_id: int) -> bool:
        return await ConversationRepository.delete(conv_id)

    @staticmethod
    async def clone_conversation(conv_id: int) -> bool:
        return await ConversationRepository.clone(conv_id)

    @staticmethod
    async def create_branch(conv_id: int, branch_point: int = 0) -> bool:
        return await ConversationRepository.create_branch(conv_id, branch_point)

    @staticmethod
    async def analyze_conversation(conv_id: int) -> str:
        return await ConversationRepository.analyze(conv_id)

    @staticmethod
    async def get_messages(conv_id: int) -> list[dict]:
        """Obtiene todos los mensajes de una conversación."""
        return await ConversationRepository.get_messages(conv_id)

    @staticmethod
    async def get_conversation(conv_id: int) -> dict | None:
        """Get conversation metadata with message count."""
        return await ConversationRepository.get_conversation(conv_id)

    @staticmethod
    async def list_conversations(limit: int = 50, offset: int = 0) -> list[dict]:
        """List conversations with pagination, newest first."""
        return await ConversationRepository.list_all(limit=limit, offset=offset)

    @staticmethod
    async def count_conversations() -> int:
        """Total number of conversations in the current workspace."""
        return await ConversationRepository.count_all()

    @staticmethod
    async def export_conversation(conv_id: int, format: str = "md"):
        return await ConversationRepository.export(conv_id, format)
