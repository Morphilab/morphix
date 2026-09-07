# El gate de Bot Mode NUNCA es fail-open ni con el opt-out invertido

import pytest


def test_bot_flags_registered_as_bool(monkeypatch):
    """Los flags de emergencia deben estar registrados como bool en _init_flags."""
    from core.feature_flags import KairosFlags

    monkeypatch.delenv("BOT_MODE", raising=False)
    monkeypatch.delenv("BOT_MODE_PROTOCOL", raising=False)
    k = object.__new__(KairosFlags)
    k._dirty_flags = set()
    k._init_flags()
    assert isinstance(k.flags.get("BOT_MODE"), bool)
    assert isinstance(k.flags.get("BOT_MODE_PROTOCOL"), bool)


def test_env_false_optout_is_honored(monkeypatch):
    """BOT_MODE=false en env debe cerrar el gate (antes: string→bool('false')==True)."""
    from core.feature_flags import KairosFlags

    monkeypatch.setenv("BOT_MODE", "false")
    k = object.__new__(KairosFlags)
    k._dirty_flags = set()
    k._init_flags()
    assert k.get("BOT_MODE") is False


@pytest.mark.parametrize("raw", ["false", "False", "FALSE", "0", "no"])
def test_gate_flag_never_truthy_from_string(raw, monkeypatch):
    """_flag() debe normalizar strings: ningún caso de 'false' da True."""
    import core.bots_gate as bg

    class _FakeKairos:
        def __init__(self):
            self.flags = {"BOT_MODE": raw}

        def get(self, key, default=None):
            return self.flags.get(key, default)

    fake = _FakeKairos()
    monkeypatch.setattr(bg, "kairos", fake)
    assert bg._flag("BOT_MODE", True) is False, f"'{raw}' no debe evaluar a True"


def test_gate_flag_missing_key_falls_back_to_default(monkeypatch):
    import core.bots_gate as bg

    class _FakeKairos:
        def get(self, key, default=None):
            return None

    monkeypatch.setattr(bg, "kairos", _FakeKairos())
    assert bg._flag("BOT_MODE", True) is True


# ── Round-trip DM: contador de envíos por turno ────────────────


@pytest.mark.asyncio
async def test_handle_dm_call_increments_turn_counter(monkeypatch):
    """El contador de send_to_bot exitosos alimenta el espejo de bots_wake:
    sube SOLO en envío exitoso, y un fallo NO lo incrementa."""
    from unittest.mock import AsyncMock

    import core.bots_gate as bg

    class _FakeKairos:
        def get(self, key, default=None):
            return True

    monkeypatch.setattr(bg, "kairos", _FakeKairos())
    monkeypatch.setattr(bg, "_roster_size", AsyncMock(return_value=2))
    ok_send = AsyncMock(return_value={"status": "sent", "to": "bravo", "position": 1})
    monkeypatch.setattr(bg, "send_dm", ok_send)

    assert bg.dm_sends_this_turn() == 0
    with bg.set_active_canonical("alfa"):
        ok, out = await bg.handle_dm_call({"target": "bravo", "message": "hola"}, "main")
        assert ok is True
        assert bg.dm_sends_this_turn() == 1
        ok2, _ = await bg.handle_dm_call({"target": "bravo", "message": "otra"}, "main")
        assert ok2 is True
        assert bg.dm_sends_this_turn() == 2, "cada envío exitoso suma"

    # fuera de sesión canónica el gate rechaza y NO suma
    rejected, _ = await bg.handle_dm_call({"target": "bravo", "message": "x"}, "main")
    assert rejected is False
    assert bg.dm_sends_this_turn() == 2

    # fallo del envío tampoco suma
    monkeypatch.setattr(bg, "send_dm", AsyncMock(side_effect=RuntimeError("boom")))
    with bg.set_active_canonical("alfa"):
        failed, _ = await bg.handle_dm_call({"target": "bravo", "message": "y"}, "main")
        assert failed is False
        assert bg.dm_sends_this_turn() == 2


@pytest.mark.asyncio
async def test_loop_does_not_activate_canonical_when_dm_disabled(monkeypatch):
    """Salas: un turno con bot_context dm_enabled=False NO fija la
    sesión canónica activa — el re-gate de handle_dm_call rechazaría cualquier
    send_to_bot alucinado desde una respuesta grupal."""
    import orchestration.loop as loop
    from core.bots_gate import _active_canonical

    seen: list[str | None] = []

    async def fake_impl(**kwargs):
        seen.append(_active_canonical.get())
        return {"result": "ok"}

    monkeypatch.setattr(loop, "_execute_agent_loop_impl", fake_impl)

    await loop.execute_agent_loop(task="t", bot_context={"slug": "alfa", "dm_enabled": False})
    assert seen[-1] is None, "dm_enabled=False no debe activar la sesión canónica"

    await loop.execute_agent_loop(task="t", bot_context={"slug": "alfa"})
    assert seen[-1] == "alfa", "sin dm_enabled=False (canónico) SÍ se activa"
