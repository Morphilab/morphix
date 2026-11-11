"""Guard: toda clave COLORS['x'] usada en desktop/ debe existir en el dict.

Las claves del dict de compatibilidad (bg_surface, border_light...) NO
coinciden 1:1 con ColorTokens (bg_solid, border_strong...): usar nombres
de token lanza KeyError en runtime que deja el Dashboard sin workflows ni
agentes. COLORS es legacy y este guard lo vigila.
"""

import re
from pathlib import Path

from desktop.theme import COLORS

_DESKTOP = Path(__file__).parent.parent / "desktop"
_COLORS_USE = re.compile(r"COLORS\['([a-z_]+)'\]")


def test_every_colors_key_referenced_in_desktop_exists():
    offenders: list[str] = []
    for py in sorted(_DESKTOP.rglob("*.py")):
        for match in _COLORS_USE.finditer(py.read_text(encoding="utf-8")):
            key = match.group(1)
            if key not in COLORS:
                offenders.append(f"{py.name}: COLORS['{key}']")
    assert not offenders, (
        "Claves COLORS inexistentes (usa el dict de compatibilidad, "
        f"no los nombres de ColorTokens): {offenders}"
    )


def test_compat_dict_has_the_known_aliases():
    # Anclas mínimas del mapping legacy → token (theme.py::_build_compat_colors)
    assert COLORS["bg_surface"]  # → ColorTokens.bg_solid
    assert COLORS["bg_surface_raised"]  # → ColorTokens.bg_raised
    assert COLORS["border_light"]  # → ColorTokens.border_strong
    assert COLORS["border_focus"]  # → ColorTokens.border_focus
