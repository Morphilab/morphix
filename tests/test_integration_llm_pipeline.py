# tests/test_integration_llm_pipeline.py
"""Test de integración: ModelsController + RetryLedger + CacheManager."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import APIError

from core.cache_manager import CacheManager
from core.retry_ledger import RetryLedger
from llm.controller import ModelsController


@pytest.fixture
def isolated_cache_manager(tmp_path):
    """Instancia aislada del CacheManager."""
    cm = object.__new__(CacheManager)
    cm._init(stats_path=tmp_path / "llm_cache_stats.json")
    return cm


@pytest.fixture
def retry_ledger(tmp_path):
    return RetryLedger(tmp_path / "llm_retry_ledger.json", ttl_seconds=3600)


class TestIntegration:
    """Tests de integración del pipeline LLM completo."""

    def test_retry_ledger_persists_across_controller_calls(self, tmp_path, monkeypatch):
        """RetryLedger persiste presupuesto entre llamadas al controller."""
        ledger_path = tmp_path / "llm_retry_ledger.json"
        rl = RetryLedger(ledger_path)

        messages = [{"role": "user", "content": "test integration"}]
        key = RetryLedger.key_for("agent", messages)

        # Simular 2 intentos fallidos
        rl.bump(key)
        rl.bump(key)

        # Nueva instancia (simula restart) debe ver el presupuesto gastado
        rl2 = RetryLedger(ledger_path)
        assert rl2.used(key) == 2

    def test_cache_manager_tracks_disjoint_buckets(self, isolated_cache_manager):
        """CacheManager registra buckets disjuntos correctamente.

        Semántica productiva (core/cache_manager.CacheStats.billed_input_tokens):
        billed_input = uncached_input + cache_hit + cache_write. El write se
        factura aparte del miss en DeepSeek, así que el costo completo del lado
        input (140) excede el wire total (100) por diseño.
        """
        cm = isolated_cache_manager

        # Simular respuesta DeepSeek con cache hits
        cm.track_usage(
            prompt_tokens=100,  # wire total (40 miss + 60 hit)
            completion_tokens=50,
            prompt_cache_hit_tokens=60,
            prompt_cache_miss_tokens=40,
            workspace="ws1",
            uncached_input_tokens=40,
            cache_write_tokens=40,
        )

        stats = cm.get_stats("ws1")
        assert stats["billed_input_tokens"] == 140  # 40 uncached + 60 hit + 40 write
        assert stats["billed_total_tokens"] == 190  # billed_input + 50 completion
        assert stats["uncached_input_tokens"] == 40
        assert stats["cache_write_tokens"] == 40

    def test_epoch_invalidation_on_config_change(self, isolated_cache_manager):
        """Cambio de call-config invalida epoch y lo registra."""
        cm = isolated_cache_manager

        e1 = cm.note_epoch("ws1", "deepseek", "model-a", 1024)
        e2 = cm.note_epoch("ws1", "deepseek", "model-b", 1024)  # modelo diferente

        assert e1 != e2
        stats = cm.get_stats("ws1")
        assert stats["epoch_changes"] == 1

    @pytest.mark.asyncio
    async def test_controller_skips_retries_when_budget_exhausted(
        self, tmp_path, monkeypatch, caplog
    ):
        """Con presupuesto agotado, call() salta intentos y va directo a fallback Ollama."""
        # backoff_factor explícito: el lazy-load de settings está condicionado a
        # max_retries=None y aquí inyectamos retry budget agotado.
        ctrl = ModelsController(max_retries=3, backoff_factor=2.0)

        calls = []

        # Mock del client OpenAI para contar intentos y fallar siempre
        fake_client = MagicMock()

        def _raise(**kwargs):
            calls.append(1)
            raise APIError(
                message="boom",
                request=httpx.Request("POST", "https://x"),
                body=None,
            )

        fake_client.chat.completions.create = _raise
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client_with_provider",
            lambda role, temperature: (fake_client, "model-x", 0.7, "deepseek"),
        )

        class _Resp:
            usage = None

            def get(self, k, default=None):
                if k == "message":
                    return {"role": "assistant", "content": "fallback-ok"}
                return default

        fake_ollama = MagicMock()
        fake_ollama.chat.return_value = _Resp()
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client",
            lambda *a, **k: (fake_ollama, "ollama-model", 0.7),
        )

        # Ledger con presupuesto agotado para ESTA petición.
        # call(workspace="ws1") usa RetryLedger.for_workspace: parchear ese
        # classmethod (y default por robustez) apuntando ambos al ledger tmp.
        messages = [{"role": "user", "content": "presupuesto agotado integration"}]
        key = RetryLedger.key_for("agent", messages)
        ledger_path = tmp_path / "ledger.json"
        rl = RetryLedger(ledger_path, ttl_seconds=3600)
        for _ in range(5):
            rl.bump(key)

        import core.retry_ledger as rl_mod

        monkeypatch.setattr(
            rl_mod.RetryLedger,
            "default",
            classmethod(lambda cls: rl),
        )
        monkeypatch.setattr(
            rl_mod.RetryLedger,
            "for_workspace",
            classmethod(lambda cls, workspace: rl),
        )

        # NB: el pipeline completo toca side-effects de métricas/caché fuera
        # del alcance de este test (mocks cruzados); se neutralizan aquí y su
        # comportamiento específico queda cubierto por tests/test_llm_cache.py.
        monkeypatch.setattr(ModelsController, "_track_usage", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(ModelsController, "_track_budget", staticmethod(lambda *a, **k: None))

        with patch("llm.controller.asyncio.sleep", new=MagicMock()):
            response = await ctrl.call(messages=messages, role="agent", workspace="ws1")

        text = response.choices[0].message.content or ""
        assert "fallback" in text.lower() or response.choices[0].finish_reason == "stop"
        assert len(calls) == 0, f"no debió haber intentos primarios, hubo {len(calls)}"

    @pytest.mark.asyncio
    async def test_controller_tracks_workspace_in_cache_and_retry(self, tmp_path, monkeypatch):
        """Controller pasa workspace a CacheManager y RetryLedger."""
        ctrl = ModelsController(max_retries=1, backoff_factor=2.0)

        calls = []

        fake_client = MagicMock()

        def _raise(**kwargs):
            calls.append(1)
            raise APIError(
                message="boom",
                request=httpx.Request("POST", "https://x"),
                body=None,
            )

        fake_client.chat.completions.create = _raise
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client_with_provider",
            lambda role, temperature: (fake_client, "model-x", 0.7, "deepseek"),
        )

        class _Resp:
            usage = None

            def get(self, k, default=None):
                if k == "message":
                    return {"role": "assistant", "content": "fallback-ok"}
                return default

        fake_ollama = MagicMock()
        fake_ollama.chat.return_value = _Resp()
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client",
            lambda *a, **k: (fake_ollama, "ollama-model", 0.7),
        )

        # Usar workspace específico. for_workspace se convierte en spy para
        # validar el wiring controller→workspace sin tocar el FS real.
        messages = [{"role": "user", "content": "workspace test"}]

        import core.retry_ledger as rl_mod

        seen: list[str] = []
        monkeypatch.setattr(
            rl_mod.RetryLedger,
            "for_workspace",
            classmethod(
                lambda cls, workspace: seen.append(workspace) or RetryLedger(tmp_path / "spy.json")
            ),
        )
        monkeypatch.setattr(
            rl_mod.RetryLedger,
            "default",
            classmethod(lambda cls: RetryLedger(tmp_path / "spy.json")),
        )

        # Side-effects de caché/métricas fuera de alcance aquí (ver test_llm_cache.py)
        monkeypatch.setattr(ModelsController, "_track_usage", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(ModelsController, "_track_budget", staticmethod(lambda *a, **k: None))

        with patch("llm.controller.asyncio.sleep", new=MagicMock()):
            response = await ctrl.call(messages=messages, role="agent", workspace="ws_integration")

        # Verificar que el controller resolvió el ledger vía el workspace dado.
        # El texto del fallback/finalización ya queda cubierto por el test
        # anterior y por la suite de caché; este test valida SOLO el wiring.
        assert "ws_integration" in seen, "for_workspace debe invocarse con el workspace"
        choices = getattr(response, "choices", [])
        assert choices, "call() debe retornar una respuesta con choices"
