# Regression: undercover no pisa la identidad de un bot canónico

"""agents/base.py llama undercover.inject_identity_prompt ANTES de insertar
el system del agente; la identidad genérica ("Mantén siempre esta identidad")
NO debe anteponerse a la identidad de un bot canónico ("Eres @alfa…"): el
modelo se presentaría como Morphix genérico en lugar del bot."""

from core.security.undercover_mode import UndercoverMode

_BOT_SYSTEM = (
    "Eres @alfa (Alfa), un bot con chat eterno propio. Tu persona:\n" "(sin SOUL definido aún)"
)


def test_identity_prompt_skipped_for_bot_system():
    """Si el system ya es identidad de bot canónico, NO anteponer Morphix."""
    uc = UndercoverMode()
    messages = [
        {"role": "system", "content": _BOT_SYSTEM},
        {"role": "user", "content": "quien eres?"},
    ]
    out = uc.inject_identity_prompt(messages)
    assert out[0]["role"] == "system"
    assert out[0]["content"].startswith("Eres @alfa"), "identidad del bot debe quedar primera"
    assert "Morphix" not in out[0]["content"], "la identidad genérica no debe pisar al bot"
    # defensivo: no mutar el input original
    assert messages[0]["content"] == _BOT_SYSTEM


def test_identity_prompt_still_injected_without_bot():
    """Sin identidad de bot, el comportamiento undercover se conserva intacto."""
    uc = UndercoverMode()
    messages = [{"role": "user", "content": "hola"}]
    out = uc.inject_identity_prompt(messages)
    assert out[0]["role"] == "system"
    assert "Morphix" in out[0]["content"], "la identidad genérica debe seguir inyectándose"


def test_identity_prompt_bot_after_generic_system_still_protected():
    """System genérico no-bot (p.ej. profile de agente) → undercover normal."""
    uc = UndercoverMode()
    messages = [
        {"role": "system", "content": "You are Conversational. You are friendly."},
        {"role": "user", "content": "hola"},
    ]
    out = uc.inject_identity_prompt(messages)
    assert "Morphix" in out[0]["content"], "agente no-bot conserva el blindaje undercover"
