# tests/test_git_operations.py
from unittest.mock import AsyncMock, patch

import pytest

from core.git_operations import auto_commit, smart_auto_commit


class TestAutoCommit:
    @pytest.mark.asyncio
    async def test_auto_commit_success(self):
        with patch("core.git_operations.safe_tool_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"output": "Commit realizado correctamente"}

            result = await auto_commit(workspace="main", project_root="test")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_auto_commit_failure(self):
        with patch("core.git_operations.safe_tool_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"output": "Error: nothing to commit"}

            result = await auto_commit(workspace="main")
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_auto_commit_runs_init_add_commit(self):
        with patch("core.git_operations.safe_tool_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"output": "Commit realizado"}

            await auto_commit(workspace="main", project_root="miapp", message="test")
            assert mock_call.call_count >= 3  # init + add + commit


class TestSmartAutoCommit:
    @pytest.mark.asyncio
    async def test_smart_auto_commit_default_message(self):
        with patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_ac:
            mock_ac.return_value = {"success": True}

            result = await smart_auto_commit(workspace="main")
            assert result["success"] is True
            # Debe llamar a auto_commit con un mensaje
            call_kwargs = mock_ac.call_args.kwargs
            assert "message" in call_kwargs

    @pytest.mark.asyncio
    async def test_smart_auto_commit_with_task_uses_llm(self):
        with (
            patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_ac,
            patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm,
        ):
            from unittest.mock import MagicMock

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "feat: implementé el nuevo endpoint"
            mock_llm.return_value = mock_response
            mock_ac.return_value = {"success": True}

            result = await smart_auto_commit(
                workspace="main",
                task_description="Crear endpoint /health",
                files_written=["src/api.py", "tests/test_api.py"],
            )
            assert result["success"] is True
            mock_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_smart_auto_commit_llm_fallback(self):
        """Si el LLM falla, usa mensaje por defecto."""
        with (
            patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_ac,
            patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm,
        ):
            mock_llm.side_effect = RuntimeError("LLM no disponible")
            mock_ac.return_value = {"success": True}

            result = await smart_auto_commit(
                workspace="main",
                task_description="Arreglar bug",
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_smart_auto_commit_rejects_rate_limit_error(self):
        """El mensaje de rate limit del LLM no debe usarse como commit."""
        with (
            patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_ac,
            patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm,
        ):
            from unittest.mock import MagicMock

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = (
                "❌ Rate limit excedido. Intenta de nuevo en unos segundos."
            )
            mock_llm.return_value = mock_response
            mock_ac.return_value = {"success": True}

            result = await smart_auto_commit(
                workspace="main",
                task_description="Agregar función de login",
            )
            assert result["success"] is True
            call_kwargs = mock_ac.call_args.kwargs
            assert "Rate limit" not in call_kwargs["message"]
            assert call_kwargs["message"].startswith("feat:")

    @pytest.mark.asyncio
    async def test_smart_auto_commit_rejects_ollama_error(self):
        """El mensaje de error de Ollama no debe usarse como commit."""
        with (
            patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_ac,
            patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm,
        ):
            from unittest.mock import MagicMock

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = (
                "❌ Ollama también falló. Verifica que esté corriendo."
            )
            mock_llm.return_value = mock_response
            mock_ac.return_value = {"success": True}

            result = await smart_auto_commit(
                workspace="main",
                task_description="Refactorizar auth",
            )
            assert result["success"] is True
            call_kwargs = mock_ac.call_args.kwargs
            assert "Ollama" not in call_kwargs["message"]
            assert call_kwargs["message"].startswith("feat:")

    @pytest.mark.asyncio
    async def test_smart_auto_commit_valid_message_passes(self):
        """Un mensaje válido no debe ser rechazado."""
        with (
            patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_ac,
            patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm,
        ):
            from unittest.mock import MagicMock

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "feat: agregar endpoint de autenticación JWT"
            mock_llm.return_value = mock_response
            mock_ac.return_value = {"success": True}

            result = await smart_auto_commit(
                workspace="main",
                task_description="Agregar auth con JWT",
            )
            assert result["success"] is True
            call_kwargs = mock_ac.call_args.kwargs
            assert "feat:" in call_kwargs["message"]
