# tests/test_context_manager.py

from core.context_manager import ContextManager


class TestEstimateTokens:
    def test_empty_messages(self):
        assert ContextManager.estimate_tokens([]) == 0

    def test_simple_message(self):
        tokens = ContextManager.estimate_tokens([{"role": "user", "content": "Hola mundo"}])
        assert tokens > 0

    def test_message_with_none_content(self):
        tokens = ContextManager.estimate_tokens([{"role": "assistant", "content": None}])
        assert tokens > 0  # overhead only

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "Eres un asistente."},
            {"role": "user", "content": "Pregunta larga " * 20},
            {"role": "assistant", "content": "Respuesta " * 30},
        ]
        tokens = ContextManager.estimate_tokens(msgs)
        assert tokens > 50


class TestCompressHistory:
    def test_compress_preserves_system_prompt(self):
        msgs = [
            {"role": "system", "content": "Sistema"},
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "U2"},
            {"role": "assistant", "content": "A2"},
        ]
        compressed = ContextManager.compress_history(msgs, max_tokens=50)
        assert len(compressed) <= len(msgs)
        assert compressed[0]["role"] == "system"

    def test_compress_keeps_recent(self):
        msgs = [{"role": "user", "content": f"Mensaje {i}"} for i in range(20)]
        compressed = ContextManager.compress_history(msgs, max_tokens=30)
        assert len(compressed) < len(msgs)

    def test_empty_input(self):
        assert ContextManager.compress_history([]) == []


class TestChunkLargeFile:
    def test_chunk_small_content(self):
        chunks = ContextManager.chunk_large_file("pequeño", "test.py")
        assert len(chunks) == 1
        assert chunks[0]["file"] == "test.py"
        assert chunks[0]["start_line"] == 1

    def test_chunk_large_content(self):
        lines = [f"Línea {i}\n" for i in range(100)]
        content = "".join(lines)
        chunks = ContextManager.chunk_large_file(content, "grande.py", chunk_size=200)
        assert len(chunks) > 1
        for c in chunks:
            assert c["file"] == "grande.py"
            assert "content" in c

    def test_chunk_empty_content(self):
        assert ContextManager.chunk_large_file("", "vacio.py") == []

    def test_chunk_overlap(self):
        lines = [f"Línea {i}\n" for i in range(50)]
        content = "".join(lines)
        chunks = ContextManager.chunk_large_file(content, "test.py", chunk_size=100)
        if len(chunks) > 1:
            # Verificar solapamiento: el end_line del chunk n-1 >= start_line del chunk n
            for i in range(1, len(chunks)):
                assert chunks[i - 1]["end_line"] >= chunks[i]["start_line"]


class TestSummarize:
    def test_short_text(self):
        result = ContextManager.summarize_for_context("Corto", max_chars=100)
        assert result == "Corto"

    def test_long_text(self):
        result = ContextManager.summarize_for_context("A" * 1000, max_chars=500)
        assert len(result) == 500
        assert result.endswith("...")

    def test_build_context_summary(self):
        msgs = [{"role": "user", "content": f"Msg {i}"} for i in range(15)]
        summary = ContextManager.build_context_summary(msgs)
        assert len(summary) > 0
