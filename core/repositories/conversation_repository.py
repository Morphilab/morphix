import asyncio
import datetime
import json
import logging
import os
import re
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import desc, func, select

from core.database import get_async_session
from core.models import Conversation, Message
from llm import models

logger = logging.getLogger(__name__)

# Watermark patterns to strip from exported content
_WATERMARK_STRIP_RE = re.compile(
    r"\[(?:ver)\.[a-f0-9]{6,16}\]"
    r"|\[(?:trace|ref|ID|morphix):[a-f0-9]{6,16}\]"
    r"|\n\n\[⏣ [a-f0-9]{6,16}\]"
    r"|\n\n<!-- trace:[a-f0-9]{6,16} -->"
    r"|\n\u200b\[[a-f0-9]{6,16}\]"
    r"|\n\n\[(?:ID|morphix):[a-f0-9]{6,16}\]"
)


def _strip_watermarks(text: str) -> str:
    """Strip all watermark patterns from exported content."""
    return _WATERMARK_STRIP_RE.sub("", text)


def _collect_project_files(base_dir: Path) -> str:
    """Collect text content of project files from a directory for export inclusion.

    Only includes files with extensions: .py, .yaml, .yml, .json, .env, .txt
    Skips __pycache__ and hidden directories.
    """
    if not base_dir.exists():
        return ""
    included_exts = {".py", ".yaml", ".yml", ".json", ".env", ".txt"}
    parts: list[str] = []
    for root, dirs, files in os.walk(str(base_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fname in sorted(files):
            fpath = Path(root) / fname
            suffix = fpath.suffix
            if suffix in included_exts or fname.endswith((".env", ".env.example")):
                try:
                    content = fpath.read_text(encoding="utf-8")
                    rel = str(fpath.relative_to(base_dir))
                    parts.append(f"\n### {rel}\n```\n{content}\n```\n")
                except (OSError, UnicodeDecodeError):
                    continue
    return "\n".join(parts)


class ConversationRepository:
    """Repositorio centralizado con todas las operaciones asíncronas."""

    # ── Save / Append ──────────────────────────────────────────────

    @staticmethod
    async def save(
        title: str,
        user_message: str,
        tags: str = "maestro",
        workflow_id: int | None = None,
        conversation_history: list[dict] | None = None,
        conversation_id: int | None = None,
    ) -> int:
        """Save a new conversation, or append to existing if conversation_id is set.

        Returns the conversation id.
        """
        if not user_message or not user_message.strip():
            raise ValueError("user_message cannot be empty")

        async with get_async_session() as session:
            now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

            if conversation_id is not None:
                conv = await session.get(Conversation, conversation_id)
                if conv is None:
                    raise ValueError(f"Conversation {conversation_id} not found")
            else:
                conv = Conversation(
                    title=title[:100], created_at=now, tags=tags, workflow_id=workflow_id
                )
                session.add(conv)
                await session.flush()

            # Add the current user message
            session.add(
                Message(conversation_id=conv.id, role="user", content=user_message, timestamp=now)
            )

            # Add conversation history entries
            if conversation_history:
                if conversation_id is not None:
                    # Resume: save the last assistant AND all agent/tool entries.
                    # All other entries already exist in the DB.
                    assistant_saved = False
                    for entry in reversed(conversation_history):
                        role = entry.get("role", "unknown")
                        content = entry.get("content", "")
                        if not content or role == "system" or role == "user":
                            continue
                        if role == "assistant" and content.strip() and not assistant_saved:
                            session.add(
                                Message(
                                    conversation_id=conv.id,
                                    role=role,
                                    content=str(content)[:8000],
                                    timestamp=now,
                                )
                            )
                            assistant_saved = True
                        elif role in ("agent", "tool"):
                            session.add(
                                Message(
                                    conversation_id=conv.id,
                                    role=role,
                                    content=str(content)[:8000],
                                    timestamp=now,
                                )
                            )
                else:
                    # New conversation: save full history minus system + first user
                    first_user_saved = False
                    for entry in conversation_history:
                        role = entry.get("role", "unknown")
                        content = entry.get("content", "")
                        if not content or role == "system":
                            continue
                        if role == "user" and not first_user_saved:
                            first_user_saved = True
                            continue
                        session.add(
                            Message(
                                conversation_id=conv.id,
                                role=role,
                                content=str(content)[:8000],
                                timestamp=now,
                            )
                        )

            logger.info(
                f"Conversation {conv.id} saved with {len(conversation_history or [])} history entries"
            )
            return conv.id

    @staticmethod
    async def add_messages(conv_id: int, messages: list[dict]) -> bool:
        """Append messages to an existing conversation."""
        if not messages:
            return False

        async with get_async_session() as session:
            conv = await session.get(Conversation, conv_id)
            if not conv:
                return False

            now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            for entry in messages:
                role = entry.get("role", "unknown")
                content = entry.get("content", "")
                if not content or role == "system":
                    continue
                session.add(
                    Message(
                        conversation_id=conv_id,
                        role=role,
                        content=str(content)[:8000],
                        timestamp=now,
                    )
                )
            logger.info(f"Appended {len(messages)} messages to conversation {conv_id}")
            return True

    # ── Queries ────────────────────────────────────────────────────

    @staticmethod
    async def get_messages(conv_id: int) -> list[dict]:
        """Obtiene todos los mensajes de una conversación."""
        async with get_async_session() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conv_id)  # type: ignore[arg-type]
                .order_by(Message.timestamp)  # type: ignore[arg-type]
            )
            result = await session.execute(stmt)
            messages = result.scalars().all()
            return [
                {"role": m.role, "content": m.content, "timestamp": m.timestamp} for m in messages
            ]

    @staticmethod
    async def get_conversation(conv_id: int) -> dict | None:
        """Get conversation metadata with message count."""
        async with get_async_session() as session:
            conv = await session.get(Conversation, conv_id)
            if not conv:
                return None
            count_stmt = select(func.count(Message.id)).where(Message.conversation_id == conv_id)  # type: ignore[arg-type]
            count_result = await session.execute(count_stmt)
            msg_count = count_result.scalar()
            return {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at,
                "tags": conv.tags,
                "workflow_id": conv.workflow_id,
                "message_count": msg_count,
            }

    @staticmethod
    async def list_all(limit: int = 50, offset: int = 0) -> list[dict]:
        """List conversations with pagination, newest first."""
        async with get_async_session() as session:
            stmt = (
                select(Conversation)
                .order_by(desc(Conversation.created_at))  # type: ignore[arg-type]
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(stmt)
            return [
                {
                    "id": conv.id,
                    "title": conv.title,
                    "created_at": conv.created_at,
                    "tags": conv.tags,
                    "workflow_id": conv.workflow_id,
                }
                for conv in result.scalars()
            ]

    @staticmethod
    async def count_all() -> int:
        """Total number of conversations in the current workspace schema."""
        async with get_async_session() as session:
            stmt = select(func.count(Conversation.id))  # type: ignore[arg-type]
            result = await session.execute(stmt)
            return result.scalar() or 0

    # ── Mutations ──────────────────────────────────────────────────

    @staticmethod
    async def update_title(conv_id: int, new_title: str) -> bool:
        async with get_async_session() as session:
            conv = await session.get(Conversation, conv_id)
            if conv:
                conv.title = new_title[:100]
                session.add(conv)
                return True
        return False

    @staticmethod
    async def delete(conv_id: int) -> bool:
        async with get_async_session() as session:
            conv = await session.get(Conversation, conv_id)
            if conv:
                await session.delete(conv)
                return True
        return False

    @staticmethod
    async def clone(conv_id: int) -> bool:
        async with get_async_session() as session:
            original = await session.get(Conversation, conv_id)
            if not original:
                return False

            new_conv = Conversation(
                title=f"Clone de {original.title}",
                created_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
                tags=original.tags,
            )
            session.add(new_conv)
            await session.flush()
            await session.refresh(new_conv)

            stmt = select(Message).where(Message.conversation_id == conv_id)  # type: ignore[arg-type]
            result = await session.execute(stmt)
            messages = result.scalars().all()

            for msg in messages:
                session.add(
                    Message(
                        conversation_id=new_conv.id,
                        role=msg.role,
                        content=msg.content,
                        timestamp=msg.timestamp,
                    )
                )
            return True

    @staticmethod
    async def create_branch(conv_id: int, branch_point: int = 0) -> bool:
        async with get_async_session() as session:
            original = await session.get(Conversation, conv_id)
            if not original:
                return False

            new_conv = Conversation(
                title=f"Branch de {original.title}",
                created_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
                tags=original.tags,
            )
            session.add(new_conv)
            await session.flush()
            await session.refresh(new_conv)

            stmt = (
                select(Message)
                .where(Message.conversation_id == conv_id)  # type: ignore[arg-type]
                .order_by(Message.timestamp)  # type: ignore[arg-type]
            )
            if branch_point > 0:
                stmt = stmt.where(Message.id <= branch_point)  # type: ignore[arg-type,operator]

            result = await session.execute(stmt)
            messages = result.scalars().all()
            for msg in messages:
                session.add(
                    Message(
                        conversation_id=new_conv.id,
                        role=msg.role,
                        content=msg.content,
                        timestamp=msg.timestamp,
                    )
                )
            return True

    @staticmethod
    async def analyze(conv_id: int) -> str:
        async with get_async_session() as session:
            conv = await session.get(Conversation, conv_id)
            if not conv:
                return "Conversación no encontrada"
            stmt = select(Message).where(Message.conversation_id == conv_id)  # type: ignore[arg-type]
            result = await session.execute(stmt)
            messages = result.scalars().all()
            history_str = "\n".join([f"{m.role}: {m.content}" for m in messages])

        try:
            prompt = f"Analiza esta conversación y extrae insights clave:\n{history_str[:3000]}"
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="default",
                temperature=0.5,
            )
            return response.choices[0].message.content
        except Exception as e:
            logging.error(f"Error analizando conversación {conv_id}: {e}")
            return f"Error en análisis: {e!s}"

    @staticmethod
    async def export(
        conv_id: int, format: str = "md", project_path: str | None = None
    ) -> str | bool:
        async with get_async_session() as session:
            conv = await session.get(Conversation, conv_id)
            if not conv:
                return False

            stmt = select(Message).where(Message.conversation_id == conv_id)  # type: ignore[arg-type]
            result = await session.execute(stmt)
            messages = result.scalars().all()

            from core.path_resolver import paths

            exports_dir = paths.exports_dir()
            exports_dir.mkdir(parents=True, exist_ok=True)

            # Sanitize title for filename
            safe_title = "".join(c if c.isalnum() or c in "_- " else "_" for c in conv.title)[
                :40
            ].strip()
            # Stable filename — re-exports overwrite previous version
            filename = str(exports_dir / f"morphix_conversacion_{conv_id}_{safe_title}.{format}")

            if format == "json":
                data = [
                    {
                        "role": m.role,
                        "content": _strip_watermarks(m.content),
                        "timestamp": m.timestamp.isoformat(),
                    }
                    for m in messages
                ]

                def _write_json():
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)

                await asyncio.to_thread(_write_json)
                return filename

            elif format == "pdf":
                data = [
                    {
                        "role": m.role,
                        "content": _strip_watermarks(m.content),
                        "timestamp": m.timestamp.isoformat(),
                    }
                    for m in messages
                ]

                def _build_pdf():
                    doc = SimpleDocTemplate(filename, pagesize=letter)
                    styles = getSampleStyleSheet()
                    story = []
                    story.append(Paragraph(f"Conversación: {conv.title}", styles["Title"]))
                    story.append(Spacer(1, 12))
                    for msg in data:
                        role = msg["role"]
                        label = {
                            "assistant": "🤖 Maestro",
                            "user": "👤 Usuario",
                            "agent": "🧠 Agente",
                            "tool": "🔧 Herramienta",
                        }.get(role, f"⚙️ {role}")
                        story.append(
                            Paragraph(
                                f"[{msg['timestamp']}] <b>{label}:</b> {msg['content']}",
                                styles["Normal"],
                            )
                        )
                        story.append(Spacer(1, 12))
                    doc.build(story)

                await asyncio.to_thread(_build_pdf)
                return filename

            elif format == "md":
                internal_phrases = (
                    "Eres Morphix",
                    "Reglas anti-frustración",
                    "Mantén siempre esta identidad",
                    "Soy Morphix, un asistente experto",
                )

                def _write_md():
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write("# Conversación Morphix\n")
                        f.write(
                            f"**ID:** {conv_id} | **Fecha:** {conv.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
                        )
                        for m in messages:
                            role = m.role
                            content = _strip_watermarks(m.content)
                            if role == "system" and any(p in content for p in internal_phrases):
                                continue
                            if role == "assistant":
                                f.write(f"**🤖 Maestro:**\n{content}\n\n---\n\n")
                            elif role == "user":
                                f.write(f"**👤 Usuario:**\n{content}\n\n---\n\n")
                            elif role == "agent":
                                f.write(f"**🧠 Agente:**\n{content}\n\n---\n\n")
                            elif role == "tool":
                                f.write(f"**🔧 Herramienta:**\n{content}\n\n---\n\n")
                            else:
                                f.write(f"**⚙️ {role}:**\n{content}\n\n---\n\n")

                        # Append actual project files from disk if available
                        if project_path:
                            proj_dir = Path(project_path)
                            file_contents = _collect_project_files(proj_dir)
                            if file_contents:
                                f.write(
                                    "\n---\n\n## 📁 Archivos del proyecto (contenido real del disco)\n\n"
                                )
                                f.write(file_contents)

                await asyncio.to_thread(_write_md)
                return filename

            elif format == "html":
                internal_phrases = (
                    "Eres Morphix",
                    "Reglas anti-frustración",
                    "Mantén siempre esta identidad",
                    "Soy Morphix, un asistente experto",
                )

                def _write_html():
                    from html import escape

                    try:
                        from pygments import highlight
                        from pygments.formatters import HtmlFormatter
                        from pygments.lexers import get_lexer_by_name, guess_lexer
                        from pygments.util import ClassNotFound

                        PYGMENTS_OK = True
                    except ImportError:
                        PYGMENTS_OK = False

                    formatter = (
                        HtmlFormatter(style="default", noclasses=True) if PYGMENTS_OK else None
                    )

                    def _highlight_code(text: str) -> str:
                        if not PYGMENTS_OK or formatter is None:
                            return f"<pre><code>{escape(text)}</code></pre>"
                        code_block_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

                        def _hl_match(m):
                            lang = m.group(1) or "python"
                            code = m.group(2)
                            try:
                                lexer = get_lexer_by_name(lang, stripall=True)
                            except ClassNotFound:
                                try:
                                    lexer = guess_lexer(code)
                                except ClassNotFound:
                                    lexer = get_lexer_by_name("text")
                            return highlight(code, lexer, formatter)

                        return code_block_pattern.sub(_hl_match, text)

                    with open(filename, "w", encoding="utf-8") as f:
                        f.write('<!DOCTYPE html>\n<html lang="es">\n<head>\n')
                        f.write('<meta charset="utf-8">\n')
                        f.write(f"<title>Conversación Morphix #{conv_id}</title>\n")
                        f.write("<style>")
                        f.write(
                            "body{font-family:Arial,Helvetica,sans-serif;max-width:900px;"
                            "margin:40px auto;padding:20px;background:#fafafa;color:#222}"
                            "h1{color:#333;border-bottom:2px solid #ddd;padding-bottom:8px}"
                            ".msg{margin:12px 0;padding:12px;border-radius:6px;background:#fff;"
                            "box-shadow:0 1px 3px rgba(0,0,0,0.1)}"
                            ".role{font-weight:bold;font-size:0.9em;color:#555}"
                            ".content{margin-top:6px;white-space:pre-wrap;line-height:1.5}"
                            "hr{border:0;border-top:1px solid #eee;margin:20px 0}"
                            ".highlight,.codehilite{background:#f4f4f4;border-radius:4px;"
                            "padding:10px;overflow-x:auto;font-size:0.9em}"
                        )
                        f.write("</style>\n</head>\n<body>\n")
                        f.write(f"<h1>Conversación Morphix #{conv_id}</h1>\n")
                        f.write(
                            f"<p><strong>ID:</strong> {conv_id} | "
                            f"<strong>Fecha:</strong> {conv.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>\n"
                            "<hr>\n"
                        )
                        for m in messages:
                            content = _strip_watermarks(m.content)
                            if m.role == "system" and any(p in content for p in internal_phrases):
                                continue
                            role_label = {
                                "assistant": "Maestro",
                                "user": "Usuario",
                                "agent": "Agente",
                                "tool": "Herramienta",
                            }.get(m.role, m.role.capitalize())
                            f.write(f'<div class="msg">\n<p class="role">{role_label}:</p>\n')
                            f.write(f'<div class="content">{_highlight_code(content)}</div>\n')
                            f.write("</div>\n<hr>\n")

                        if project_path:
                            proj_dir = Path(project_path)
                            file_contents = _collect_project_files(proj_dir)
                            if file_contents:
                                f.write(
                                    "<h2>Archivos del proyecto (contenido real del disco)</h2>\n"
                                )
                                f.write(f"<pre><code>{escape(file_contents)}</code></pre>\n")

                        f.write("</body>\n</html>")

                await asyncio.to_thread(_write_html)
                return filename

        return False
