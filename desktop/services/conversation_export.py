"""Exportación de conversaciones (md/json/pdf/html) — extraída de MaestroTab."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from agents.audit import redact_credentials

logger = logging.getLogger(__name__)

INTERNAL_MARKERS = (
    "Eres Morphix",
    "Reglas anti-frustración",
    "Mantén siempre esta identidad",
    "Soy Morphix, un asistente experto",
)

# Misma protección anti-watermark que core/repositories (paridad).
_WATERMARK_STRIP_RE = None


def _strip_watermarks(text: str) -> str:
    """Elimina marcas de versión tipo [ver.<hex>] del contenido exportado."""
    import re

    global _WATERMARK_STRIP_RE  # noqa: PLW0603
    if _WATERMARK_STRIP_RE is None:
        _WATERMARK_STRIP_RE = re.compile(r"\s*\[ver\.[0-9a-f]{6,}\]")
    return _WATERMARK_STRIP_RE.sub("", text)


# Todo contenido de mensaje escrito al archivo exportado pasa por aquí.
def _sanitize(text: str) -> str:
    return redact_credentials(_strip_watermarks(text))


def _is_internal(msg: dict) -> bool:
    return msg.get("role") == "system" and any(
        p in msg.get("content", "") for p in INTERNAL_MARKERS
    )


async def export_history_to_file(history: list[dict[str, Any]], filename: str, fmt: str) -> str:
    """Exporta history a filename en formato fmt (md|json|pdf|html). Retorna filename."""
    if fmt not in ("md", "json", "pdf", "html"):
        raise ValueError(f"Formato no soportado: {fmt}")
    if fmt == "json":
        data = [
            {
                "role": m.get("role", "?"),
                "content": _sanitize(m.get("content", "")),
                "agent": m.get("agent"),
                "label": m.get("label"),
            }
            for m in history
            if not _is_internal(m)
        ]
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    elif fmt == "md":
        with open(filename, "w", encoding="utf-8") as f:
            f.write("# Conversación Morphix\n")
            f.write(
                f"**Fecha:** {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
            )
            for msg in history:
                role = msg.get("role", "?")
                content = _sanitize(msg.get("content", ""))
                if _is_internal(msg):
                    continue
                if role == "assistant":
                    f.write(f"**🤖 Maestro:**\n{content}\n\n---\n\n")
                elif role == "user":
                    f.write(f"**👤 Usuario:**\n{content}\n\n---\n\n")
                elif role == "agent":
                    agent = msg.get("agent", "agente")
                    label = msg.get("label", "")
                    f.write(f"**🧠 {agent.capitalize()} ({label}):**\n{content}\n\n---\n\n")
                elif role == "tool":
                    f.write(f"**🔧 Herramienta:**\n{content}\n\n---\n\n")
                else:
                    f.write(f"**⚙️ {role}:**\n{content}\n\n---\n\n")

    elif fmt == "pdf":
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        data = [
            {**m, "content": _sanitize(m.get("content", ""))}
            for m in history
            if not _is_internal(m)
        ]

        doc = SimpleDocTemplate(filename, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        story.append(Paragraph("Conversación Morphix", styles["Title"]))
        story.append(Spacer(1, 12))
        for msg in data:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            label = {
                "assistant": "🤖 Maestro",
                "user": "👤 Usuario",
                "agent": f"🧠 {msg.get('agent', 'agente').capitalize()}",
                "tool": "🔧 Herramienta",
            }.get(role, f"⚙️ {role}")
            story.append(Paragraph(f"<b>{label}:</b> {content}", styles["Normal"]))
            story.append(Spacer(1, 12))
        doc.build(story)

    elif fmt == "html":
        from html import escape

        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import get_lexer_by_name, guess_lexer
            from pygments.util import ClassNotFound

            formatter = HtmlFormatter(style="default", noclasses=True)

            def _render_html(text: str) -> str:
                """Escapa TODO lo que está FUERA de fences; los fences
                los renderiza pygments (que ya HTML-escapa el código)."""
                import re

                parts: list[str] = []
                last = 0
                for m in re.finditer(r"```(\w*)\n(.*?)```", text, flags=re.DOTALL):
                    parts.append(escape(text[last : m.start()]))
                    lang = m.group(1) or "python"
                    code = m.group(2)
                    try:
                        lexer = get_lexer_by_name(lang, stripall=True)
                    except ClassNotFound:
                        try:
                            lexer = guess_lexer(code)
                        except ClassNotFound:
                            lexer = get_lexer_by_name("text")
                    parts.append(highlight(code, lexer, formatter))
                    last = m.end()
                parts.append(escape(text[last:]))
                return "".join(parts)

        except ImportError:

            def _render_html(text: str) -> str:
                return f"<pre><code>{escape(text)}</code></pre>"

        with open(filename, "w", encoding="utf-8") as f:
            f.write('<!DOCTYPE html>\n<html lang="es">\n<head>\n')
            f.write('<meta charset="utf-8">\n')
            f.write("<title>Conversación Morphix</title>\n")
            f.write("<style>")
            f.write(
                "body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;"
                "padding:20px;background:#fafafa;color:#222}"
                "h1{color:#333;border-bottom:2px solid #ddd;padding-bottom:8px}"
                ".msg{margin:12px 0;padding:12px;border-radius:6px;background:#fff;"
                "box-shadow:0 1px 3px rgba(0,0,0,.1)}"
                ".role{font-weight:bold;font-size:.9em;color:#555}"
                ".content{margin-top:6px;line-height:1.5}"
                "hr{border:0;border-top:1px solid #eee;margin:20px 0}"
                ".highlight{background:#f4f4f4;border-radius:4px;padding:10px;"
                "overflow-x:auto;font-size:.9em}"
            )
            f.write("</style>\n</head>\n<body>\n")
            f.write("<h1>Conversación Morphix</h1>\n")
            f.write(
                f"<p><strong>Fecha:</strong> "
                f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}</p>\n"
                "<hr>\n"
            )
            for msg in history:
                role = msg.get("role", "unknown")
                content = _sanitize(msg.get("content", ""))
                if _is_internal(msg):
                    continue
                role_label = {
                    "assistant": "Maestro",
                    "user": "Usuario",
                    "agent": "Agente",
                    "tool": "Herramienta",
                }.get(role, role.capitalize())
                f.write(f'<div class="msg">\n<p class="role">{role_label}:</p>\n')
                f.write(f'<div class="content">{_render_html(content)}</div>\n')
                f.write("</div>\n<hr>\n")
            f.write("</body>\n</html>")

    return filename
