"""Context Manager — gestión inteligente de ventana de contexto para LLMs."""


class ContextManager:
    """Gestión de ventana de contexto: estimación de tokens, compresión, chunking."""

    # Approximation: ~4 chars per token for English, ~3 for Spanish/code
    CHARS_PER_TOKEN = 3.5

    @classmethod
    def _max_tokens(cls) -> int:
        from core.config import settings

        return settings.max_context_tokens

    @classmethod
    def estimate_tokens(cls, messages: list[dict]) -> int:
        """Estima el número de tokens en una lista de mensajes."""
        total: float = 0.0
        for msg in messages:
            content = msg.get("content", "")
            if content is None:
                content = ""
            total += len(str(content)) / cls.CHARS_PER_TOKEN
            # Add per-message overhead (~4 tokens)
            total += 4
        return int(total)

    @classmethod
    def compress_history(cls, messages: list[dict], max_tokens: int = 8000) -> list[dict]:
        """Comprime el historial manteniendo system prompt y últimos mensajes."""
        if not messages:
            return []

        system = messages[0] if messages and messages[0].get("role") == "system" else None
        rest = messages[1:] if system else messages

        # Mantener system prompt
        result = [system] if system else []

        # Take recent messages until budget is filled
        taken: list = []
        used = cls.estimate_tokens(result)
        for msg in reversed(rest):
            msg_tokens = cls.estimate_tokens([msg])
            if used + msg_tokens > max_tokens:
                break
            taken.insert(0, msg)
            used += msg_tokens

        return result + taken

    @classmethod
    def chunk_large_file(cls, content: str, file_path: str, chunk_size: int = 2000) -> list[dict]:
        """Divide archivos grandes en chunks solapados con metadatos."""
        lines = content.splitlines(keepends=True)
        if not lines:
            return []

        chunks = []
        start_line = 0
        chunk_idx = 0

        while start_line < len(lines):
            chunk_lines: list = []
            current_len = 0
            end_line = start_line

            for i in range(start_line, len(lines)):
                if current_len + len(lines[i]) > chunk_size and chunk_lines:
                    break
                chunk_lines.append(lines[i])
                current_len += len(lines[i])
                end_line = i + 1

            chunk_text = "".join(chunk_lines)
            chunks.append(
                {
                    "file": file_path,
                    "chunk_index": chunk_idx,
                    "start_line": start_line + 1,
                    "end_line": end_line,
                    "content": chunk_text,
                    "size": len(chunk_text),
                }
            )

            start_line = end_line
            chunk_idx += 1

            # Overlap: last line of previous chunk starts the next
            if end_line < len(lines):
                start_line = max(start_line - 1, 0)

        return chunks

    @classmethod
    def summarize_for_context(cls, text: str, max_chars: int = 500) -> str:
        """Resume un texto para incluirlo en contexto limitado."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    @classmethod
    def build_context_summary(cls, messages: list[dict], max_tokens: int = 2000) -> str:
        """Construye un resumen del historial para inyectar en nuevo contexto."""
        if not messages:
            return ""
        parts = []
        for msg in messages[-10:]:
            content = msg.get("content", "")
            if content:
                role = msg.get("role", "?")
                parts.append(f"[{role}]: {cls.summarize_for_context(str(content), 200)}")
        return "\n".join(parts)
