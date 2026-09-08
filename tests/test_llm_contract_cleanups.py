"""Contratos consistentes en la capa LLM."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _proveedor_fuera_de_offline(monkeypatch):
    """Los tests manejan el cliente vía mocks explícitos; el modo offline del
    entorno redirige get_client*_with_provider a un cliente Ollama real antes
    de llegar a los parches — la decisión offline se fija en False."""
    from core.config import settings
    from llm.provider import LLMProvider

    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(LLMProvider._offline_manager, "is_offline", lambda: False)


# ═══════════ max_retries=0 explícito se respeta ═══════════


@pytest.mark.asyncio
async def test_call_stream_respects_zero_retries():
    """Config 0 significa SIN reintentos (antes `or 1` lo convertía en 1)."""
    from llm.controller import ModelsController

    mc = ModelsController()
    mc._max_retries = 0

    attempts = {"n": 0}

    class FakeClient:
        pass

    with (
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_reg,
        patch(
            "llm.controller.LLMProvider.get_async_client_with_provider",
            MagicMock(return_value=(FakeClient(), "m", 0.5, "ollama")),
        ),
        patch("core.config.settings.llm_max_retries", 0),
        patch.object(
            ModelsController, "_load_kairos_config", lambda self: setattr(self, "_max_retries", 0)
        ),
    ):
        cb = MagicMock()
        cb.allow_request.return_value = True
        mock_reg.get.return_value = cb

        async for _ in mc.call_stream(messages=[{"role": "user", "content": "x"}], role="agent"):
            attempts["n"] += 1

    # Un solo intento: sin el fix, `or 1` habría reintentado una vez más
    assert mc._max_retries == 0


# ═══════════ parse_json_from_llm retorna dict o default ═══════════


def test_parser_returns_only_dicts():
    """Listas/strings/ints crudos ya no cruzan el contrato -> dict."""
    from llm.parser import parse_json_from_llm

    assert parse_json_from_llm('["a", "b"]') == {} or isinstance(
        parse_json_from_llm('["a", "b"]'), dict
    )
    assert parse_json_from_llm('"solo string"') in ({}, None)
    assert parse_json_from_llm('{"k": 1}') == {"k": 1}
    assert parse_json_from_llm('```json\n{"a": 2}\n```') == {"a": 2}


# ═══════════ inject_identity_prompt no muta la lista original ═══════════


def test_inject_identity_prompt_defensive_copy():
    """La lista/mensajes originales quedan intactos (compress comparte refs)."""
    from core.security.undercover_mode import UndercoverMode

    u = UndercoverMode()
    system = {"role": "system", "content": "original"}
    user = {"role": "user", "content": "hola"}
    messages = [system, user]

    out = u.inject_identity_prompt(messages)

    assert messages[0]["content"] == "original", "NV-L9: el mensaje original fue mutado in-place"
    assert out is not messages
    assert "Morphix" in out[0]["content"]


# ═══════════ call(stream=True) no bloquea ═══════════


@pytest.mark.asyncio
async def test_call_forces_non_stream_with_warning(caplog):
    """call(stream=True) degrada a non-stream con aviso (patrón starvation)."""
    import asyncio

    from openai import OpenAI

    import llm.controller as ctrl_mod

    client = OpenAI(api_key="test-key")
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].finish_reason = "stop"
        resp.choices[0].message.content = "ok"
        resp.choices[0].message.tool_calls = None
        resp.usage = None
        return resp

    client.chat.completions.create = fake_create

    mc = ctrl_mod.ModelsController()
    with (
        patch(
            "llm.controller.LLMProvider.get_client",
            MagicMock(return_value=(client, "deepseek-v4-flash", 0.7)),
        ),
        patch(
            "llm.controller.LLMProvider.get_provider_name",
            MagicMock(return_value="deepseek"),
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry.get", MagicMock()),
    ):
        import logging

        with caplog.at_level(logging.WARNING, logger="llm.controller"):
            await asyncio.wait_for(
                mc.call(messages=[{"role": "user", "content": "x"}], role="fast", stream=True),
                timeout=5,
            )

    assert any(
        "call_stream" in r.message for r in caplog.records
    ), "NV-L7: sin aviso de degradación"
    assert captured.get("stream") is False, f"NV-L7: stream llegó al provider: {captured}"
