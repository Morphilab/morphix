# tests/test_bots_no_pointers.py — gate anti-punteros + cableado canónico
"""Sin BD: el gate rechaza punteros y el cableado no usa recencia.

- Cero punteros: ``ui_meta`` jamás persiste ids de conversación o
  sesión. ``FORBIDDEN_UI_META_KEYS`` + ``_ensure_no_pointer_keys`` en
  ``core/bots.py`` (aplicado en ``set_ui_meta``) son el tripwire: si una
  ruta futura intenta persistir un puntero, esto falla.
- La recencia nunca gana: preview del roster y apertura usan la MISMA
  resolución canónica server-side. Test de cableado sobre
  ``desktop/services/bots_service.py`` (sin Qt, sin BD).
"""

from pathlib import Path

import pytest

from core.bots import (
    FORBIDDEN_UI_META_KEYS,
    BotError,
    _ensure_no_pointer_keys,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── El gate rechaza cada clave prohibida ───────────────────────────────


@pytest.mark.parametrize("key", sorted(FORBIDDEN_UI_META_KEYS))
def test_pointer_key_rejected(key: str):
    """Cada clave del conjunto prohibido es rechazada con error."""
    with pytest.raises(BotError, match="punteros"):
        _ensure_no_pointer_keys({"avatar": "a", key: 123})


def test_clean_ui_meta_passes():
    """ui_meta normal (avatar, grupos, títulos) pasa el gate."""
    _ensure_no_pointer_keys(
        {"avatar": "a", "pinned_chats": [], "group_ids": ["r1"], "title": "Bot"}
    )


def test_non_dict_rejected():
    """ui_meta no-dict se rechaza antes de tocar la BD."""
    with pytest.raises(BotError):
        _ensure_no_pointer_keys(["conversation_id"])  # type: ignore[arg-type]


# ── Ninguna otra escritura de punteros en core/bots.py ─────────────────


def test_no_pointer_literals_outside_forbidden_set():
    """Cada literal prohibido aparece UNA vez: en FORBIDDEN_UI_META_KEYS.

    Si alguien añade una escritura de puntero en este módulo, el literal se
    repite y esto falla (anti-drift textual, como test_alembic_baseline).
    """
    text = (REPO_ROOT / "core" / "bots.py").read_text(encoding="utf-8")
    for key in sorted(FORBIDDEN_UI_META_KEYS):
        assert text.count(f'"{key}"') == 1, (
            f'"{key}" aparece != 1 vez en core/bots.py: '
            "solo puede vivir en FORBIDDEN_UI_META_KEYS"
        )


# ── Preview == apertura por construcción ───────────────────────────────


def _service_source() -> str:
    return (REPO_ROOT / "desktop" / "services" / "bots_service.py").read_text(encoding="utf-8")


def test_roster_preview_uses_canonical_resolution():
    """El preview NO ordena por recencia: resuelve por índice canónico."""
    src = _service_source()
    assert "resolve_canonical" in src
    lowered = src.lower()
    assert (
        "order_by" not in lowered and "order by" not in lowered
    ), "Regresión: el roster no puede ordenar por recencia"


def test_open_uses_adopt_before_mint():
    """La apertura acuña vía ensure_open (adopt-before-mint), no por id."""
    src = _service_source()
    assert "ensure_open" in src
