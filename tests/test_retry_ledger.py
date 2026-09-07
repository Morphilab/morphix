# tests/test_retry_ledger.py
"""Tests del Retry Ledger — contador de reintentos LLM persistido."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIError

from core.retry_ledger import RetryLedger


@pytest.fixture()
def ledger(tmp_path):
    return RetryLedger(tmp_path / "llm_retry_ledger.json", ttl_seconds=3600)


MSG = [{"role": "user", "content": "hola"}]


class TestKey:
    def test_stable_per_request(self):
        a = RetryLedger.key_for("agent", MSG)
        b = RetryLedger.key_for("agent", [dict(m) for m in MSG])
        assert a == b
        assert a.startswith("agent:")

    def test_differs_by_role_or_content(self):
        assert RetryLedger.key_for("agent", MSG) != RetryLedger.key_for("fast", MSG)
        assert RetryLedger.key_for("agent", MSG) != RetryLedger.key_for(
            "agent", [{"role": "user", "content": "otra"}]
        )


class TestPersistence:
    def test_bump_persists_across_instances(self, tmp_path):
        path = tmp_path / "ledger.json"
        rl1 = RetryLedger(path)
        key = RetryLedger.key_for("agent", MSG)
        rl1.bump(key)
        rl1.bump(key)
        # Simula restart: instancia nueva sobre el mismo archivo
        rl2 = RetryLedger(path)
        assert rl2.used(key) == 2, "el presupuesto debe sobrevivir el reinicio"

    def test_clear_on_success(self, ledger):
        key = RetryLedger.key_for("agent", MSG)
        ledger.bump(key)
        ledger.clear(key)
        assert ledger.used(key) == 0

    def test_expired_entry_is_fresh(self, tmp_path):
        path = tmp_path / "ledger.json"
        rl = RetryLedger(path, ttl_seconds=10)
        key = RetryLedger.key_for("agent", MSG)
        rl.bump(key)
        # Fabricar antigüedad más allá del TTL
        data = json.loads(path.read_text())
        data[key]["last_ts"] = time.time() - 1000
        data[key]["first_ts"] = data[key]["last_ts"]
        path.write_text(json.dumps(data))
        assert rl.used(key) == 0, "entrada expirada no debe consumir presupuesto"

    def test_ttl_window_reset(self, tmp_path):
        """Un bump tras TTL desde first_ts reinicia la cuenta."""
        path = tmp_path / "ledger.json"
        rl = RetryLedger(path, ttl_seconds=10)
        key = RetryLedger.key_for("agent", MSG)
        rl.bump(key)  # count=1
        now = time.time()
        data = json.loads(path.read_text())
        data[key]["last_ts"] = now - 500  # fuera de ventana
        data[key]["first_ts"] = data[key]["last_ts"]
        path.write_text(json.dumps(data))
        count = rl.bump(key)
        assert count == 1, "bump fuera de TTL debe empezar cuenta nueva"


class TestRobustez:
    def test_corrupt_file_tolerated(self, tmp_path):
        path = tmp_path / "ledger.json"
        path.write_text("{broken")
        rl = RetryLedger(path)
        key = RetryLedger.key_for("agent", MSG)
        assert rl.used(key) == 0
        rl.bump(key)
        assert json.loads(path.read_text())[key]["count"] == 1

    def test_atomic_no_tmp_left(self, tmp_path):
        rl = RetryLedger(tmp_path / "ledger.json")
        key = RetryLedger.key_for("agent", MSG)
        rl.bump(key)
        rl.clear(key)
        assert not list(tmp_path.glob("*.tmp"))

    def test_gc_removes_only_expired(self, tmp_path):
        path = tmp_path / "ledger.json"
        rl = RetryLedger(path, ttl_seconds=10)
        k_new = RetryLedger.key_for("agent", MSG)
        k_old = RetryLedger.key_for("agent", [{"role": "user", "content": "vieja"}])
        rl.bump(k_new)
        rl.bump(k_old)
        data = json.loads(path.read_text())
        data[k_old]["last_ts"] = time.time() - 9999
        path.write_text(json.dumps(data))
        removed = rl.gc()
        assert removed == 1
        assert rl.used(k_new) == 1
        assert rl.used(k_old) == 0


class TestControllerWiring:
    async def test_exhausted_budget_skips_attempts(self, tmp_path, monkeypatch, caplog):
        """Con presupuesto agotado (persistido), call() no reintenta: cae directo al fallback Ollama.

        Mockeamos el ledger para una clave conocida y espiamos cuántas veces se
        intenta llamar al cliente OpenAI (debe ser 0).
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx
        from openai import APIError

        import core.retry_ledger as rl_mod
        from llm.controller import ModelsController

        ctrl = ModelsController(max_retries=3)

        calls: list[int] = []

        # Patch del client OpenAI para contar intentos y fallar siempre
        fake_client = MagicMock()

        def _raise(**kwargs):
            calls.append(1)
            raise APIError(
                message="boom",
                request=httpx.Request("POST", "https://x"),
                body=None,
            )

        fake_client.chat.completions.create = _raise
        # el controller despacha por isinstance(client, OpenAI): sin este
        # parche el MagicMock caeria a la rama Ollama y nunca llamaria al fake
        monkeypatch.setattr("llm.controller.OpenAI", MagicMock)
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client_with_provider",
            lambda role, temperature: (fake_client, "model-x", 0.7, "deepseek"),
        )

        class _Resp:
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

        # Ledger con presupuesto agotado para ESTA petición
        messages = [{"role": "user", "content": "presupuesto agotado"}]
        key = RetryLedger.key_for("agent", messages)
        ledger_path = tmp_path / "ledger.json"
        rl = RetryLedger(ledger_path, ttl_seconds=3600)
        for _ in range(5):
            rl.bump(key)

        monkeypatch.setattr(rl_mod.RetryLedger, "default", classmethod(lambda cls: rl))

        with patch("llm.controller.asyncio.sleep", new=AsyncMock()):
            response = await ctrl.call(messages=messages, role="agent")

        text = response.choices[0].message.content or ""
        assert "fallback" in text.lower() or response.choices[0].finish_reason == "stop"
        assert len(calls) == 0, f"no debió haber intentos primarios, hubo {len(calls)}"


class TestControllerWritePath:
    """El controller debe ESCRIBIR el ledger (bump en fallo, clear
    en éxito) — no solo leerlo: una mitad escritora ausente dejaría el
    ledger en None y el retry sería ciego."""

    def _patch_common(self, monkeypatch, rl, fake_client, fake_ollama):
        import core.retry_ledger as rl_mod
        from llm.controller import ModelsController

        monkeypatch.setattr(rl_mod.RetryLedger, "default", classmethod(lambda cls: rl))
        monkeypatch.setattr(rl_mod.RetryLedger, "for_workspace", classmethod(lambda cls, ws: rl))
        # el controller despacha por isinstance(client, OpenAI): sin este
        # parche el MagicMock caeria a la rama Ollama y nunca llamaria al fake
        monkeypatch.setattr("llm.controller.OpenAI", MagicMock)
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client_with_provider",
            lambda role, temperature: (fake_client, "model-x", 0.7, "deepseek"),
        )
        monkeypatch.setattr(
            "llm.controller.LLMProvider.get_client",
            lambda *a, **k: (fake_ollama, "ollama-m", 0.7),
        )
        monkeypatch.setattr(ModelsController, "_track_usage", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(ModelsController, "_track_budget", staticmethod(lambda *a, **k: None))

    @staticmethod
    def _failing_client(calls: list):
        fake = MagicMock()

        def _raise(**kwargs):
            calls.append(1)
            raise APIError(message="boom", request=httpx.Request("POST", "https://x"), body=None)

        fake.chat.completions.create = _raise
        return fake

    @staticmethod
    def _fake_ollama():
        class _Resp:
            usage = None

            def get(self, k, default=None):
                if k == "message":
                    return {"role": "assistant", "content": "fallback-ok"}
                return default

        m = MagicMock()
        m.chat.return_value = _Resp()
        return m

    @pytest.mark.asyncio
    async def test_call_fallida_bump_en_disco(self, tmp_path, monkeypatch):
        from llm.controller import ModelsController

        ledger_path = tmp_path / "ledger.json"
        rl = RetryLedger(ledger_path)
        calls: list = []
        self._patch_common(monkeypatch, rl, self._failing_client(calls), self._fake_ollama())

        ctrl = ModelsController(max_retries=2, backoff_factor=1.0)
        messages = [{"role": "user", "content": "dsh2-h1 bump"}]
        key = RetryLedger.key_for("agent", messages)

        with patch("llm.controller.asyncio.sleep", new=AsyncMock()):
            await ctrl.call(messages=messages, role="agent")

        assert len(calls) == 2, "debió intentar 2 veces el cliente primario"
        on_disk = json.loads(ledger_path.read_text())
        assert on_disk.get(key, {}).get("count", 0) >= 2, (
            "DSH2-H1: el controller no ESCRIBE el ledger en fallo — la feature "
            "durable es inerte (un crash reinicia el presupuesto)"
        )

    @pytest.mark.asyncio
    async def test_call_exitosa_limpia_ledger_en_disco(self, tmp_path, monkeypatch):
        from llm.controller import ModelsController

        ledger_path = tmp_path / "ledger.json"
        rl = RetryLedger(ledger_path)
        messages = [{"role": "user", "content": "dsh2-h1 clear"}]
        key = RetryLedger.key_for("agent", messages)
        rl.bump(key)

        ok_client = MagicMock()
        ok_resp = MagicMock()
        ok_resp.choices = [MagicMock()]
        ok_resp.usage = None
        ok_client.chat.completions.create.return_value = ok_resp
        self._patch_common(monkeypatch, rl, ok_client, self._fake_ollama())

        ctrl = ModelsController(max_retries=2, backoff_factor=1.0)
        with patch("llm.controller.asyncio.sleep", new=AsyncMock()):
            await ctrl.call(messages=messages, role="agent")

        on_disk = json.loads(ledger_path.read_text())
        assert on_disk.get(key, {}).get("count", 0) == 0, (
            "DSH2-H1: el éxito no limpia el presupuesto persistido — la "
            "siguiente llamada arrancaría con intentos fantasma gastados"
        )
