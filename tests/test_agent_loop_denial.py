# tests/test_agent_loop_denial.py — split de test_agent_loop (denegación/contexto)
"""Denegación del limiter (no-veneno) y política projectless del loop."""

from unittest.mock import MagicMock, patch

import pytest
from _agent_loop_support import mock_agent_deps  # noqa: F401 — fixture compartida


class TestRateLimitDenialNoVenenoso:
    """La denegación del limiter jamás se convierte en respuesta del agente:
    el aviso del controller es un fallo reintentable, no texto final."""

    @staticmethod
    def _notice_stream():
        from llm.controller import StreamChunk

        async def gen():
            yield StreamChunk(
                text="⚠️ Rate limit excedido. Espera unos segundos y vuelve a intentarlo.",
                finish_reason="stop",
                system_notice=True,
            )

        return gen()

    @pytest.mark.asyncio
    async def test_aviso_se_reintenta_y_la_respuesta_real_gana(self, mock_agent_deps):
        """1ª iteración denegada → reintento de la misma iteración → la 2ª
        pasada entrega la respuesta real del modelo (jamás el aviso)."""
        mock_llm, _, _ = mock_agent_deps

        real_response = MagicMock()
        real_response.choices = [MagicMock()]
        real_response.choices[0].message.content = "Respuesta legítima del modelo."
        real_response.choices[0].message.tool_calls = None
        mock_llm.side_effect = [real_response]

        llamadas = {"n": 0}

        def stream_factory(*args, **kwargs):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                return self._notice_stream()

            async def ok_stream():
                from llm.controller import StreamChunk

                yield StreamChunk(text="Respuesta ")
                yield StreamChunk(text="legítima del modelo.", is_done=True, finish_reason="stop")

            return ok_stream()

        with patch("orchestration.loop.models.call_stream", side_effect=stream_factory):
            from orchestration.loop import execute_agent_loop

            result = await execute_agent_loop(
                task="Di algo",
                agent_type="conversacional",
                workspace="main",
                on_stream_chunk=lambda text: None,
            )

        assert result["status"] == "completed"
        assert "Rate limit" not in str(result.get("result", ""))
        assert "legítima" in str(result.get("result", ""))
        assert llamadas["n"] == 2

    @pytest.mark.asyncio
    async def test_denegacion_persistente_falla_loud(self, mock_agent_deps):
        """Aviso en las dos pasadas → LLMDeniedError (el step falla honesto)."""
        mock_llm, _, _ = mock_agent_deps

        with patch(
            "orchestration.loop.models.call_stream",
            side_effect=lambda *a, **k: self._notice_stream(),
        ):
            from orchestration.loop import LLMDeniedError, execute_agent_loop

            with pytest.raises(LLMDeniedError):
                await execute_agent_loop(
                    task="Di algo",
                    agent_type="conversacional",
                    workspace="main",
                    on_stream_chunk=lambda text: None,
                )

    @pytest.mark.asyncio
    async def test_error_response_no_streaming_falla_loud(self, mock_agent_deps):
        """La ruta no-streaming trata response.error como fallo, no como texto."""
        mock_llm, _, _ = mock_agent_deps

        error_response = MagicMock()
        error_response.error = "Rate limit excedido. Intenta de nuevo en unos segundos."
        error_response.choices = [MagicMock()]
        error_response.choices[0].message.content = "❌ Rate limit excedido."
        error_response.choices[0].message.tool_calls = None
        mock_llm.return_value = error_response

        from orchestration.loop import LLMDeniedError, execute_agent_loop

        with pytest.raises(LLMDeniedError):
            await execute_agent_loop(
                task="Di algo",
                agent_type="conversacional",
                workspace="main",
            )


class TestContextoProjectless:
    @pytest.mark.asyncio
    async def test_root_punto_no_indexa_codebase(self, mock_agent_deps):
        """project_root '.' no es un proyecto real: indexar resolvería la
        base al directorio de memoria del workspace (índice frío = minutos
        de embeddings bloqueando el primer turno del agente)."""
        from orchestration.loop import _build_extra_context

        with patch("orchestration.loop.CodebaseIndexer") as mock_idx_cls:
            mock_idx = mock_idx_cls.return_value
            mock_idx.find_relevant_code.return_value = ""

            await _build_extra_context("tarea", ".", "main")
            mock_idx_cls.assert_not_called()

            await _build_extra_context("tarea", "code_projects/app", "main")
            mock_idx_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_root_vacio_no_indexa_codebase(self, mock_agent_deps):
        from orchestration.loop import _build_extra_context

        with patch("orchestration.loop.CodebaseIndexer") as mock_idx_cls:
            await _build_extra_context("tarea", "", "main")
            mock_idx_cls.assert_not_called()
