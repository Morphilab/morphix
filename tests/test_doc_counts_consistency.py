"""Guard anti-regresión de los conteos de tools citados en la capa pública.

La capa pública miente sobre los conteos cuando nadie la revisa: cada
doc que cita cuántas tools existen debe coincidir con el registry real.
El conteo de referencia es ``len(TOOL_DEFINITIONS)`` (las "function-calling
tools"); las "registered" suman ``ask_clarification``, interception-only.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _n_tools() -> int:
    from tools.specs import TOOL_DEFINITIONS

    return len(TOOL_DEFINITIONS)


def test_user_guide_tools_md_counts_match_registry():
    src = (REPO / "docs/user-guide/tools.md").read_text(encoding="utf-8")
    m = re.search(
        r"Morphix provides \*\*(\d+) registered tools\*\* — the \*\*(\d+) function-calling tools\*\*",
        src,
    )
    assert m, (
        "tools.md ya no cita los conteos con la frase "
        "'Morphix provides **N registered tools** — the **M function-calling tools**' "
        "— actualizar el patrón de este guard"
    )
    n = _n_tools()
    assert int(m.group(1)) == n + 1, (
        f"drift documental: tools.md dice {m.group(1)} registered, "
        f"el registro tiene {n} + ask_clarification = {n + 1}"
    )
    assert int(m.group(2)) == n, (
        f"drift documental: tools.md dice {m.group(2)} function-calling, "
        f"TOOL_DEFINITIONS tiene {n}"
    )


def test_readme_counts_match_registry():
    src = (REPO / "README.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+)\*\* registered \((\d+) spec'd", src)
    assert m, (
        "README ya no cita los conteos con la frase '**N registered (M spec'd …' "
        "— actualizar el patrón de este guard"
    )
    n = _n_tools()
    assert (
        int(m.group(1)) == n + 1
    ), f"drift documental: README dice {m.group(1)} registered, real {n + 1}"
    assert int(m.group(2)) == n, f"drift documental: README dice {m.group(2)} spec'd, real {n}"


def test_manual_counts_match_registry():
    src = (REPO / "docs/MANUAL_DE_PRUEBAS.md").read_text(encoding="utf-8")
    n = _n_tools()
    for pattern, idioma in (
        (r"Covers the \*\*(\d+) tools\*\*", "EN"),
        (r"Cubre los \*\*(\d+) tools\*\*", "ES"),
    ):
        m = re.search(pattern, src)
        assert m, (
            f"MANUAL_DE_PRUEBAS ya no cita el conteo de tools en {idioma} "
            f"('{pattern}') — actualizar el patrón de este guard"
        )
        assert (
            int(m.group(1)) == n
        ), f"drift documental ({idioma}): MANUAL dice {m.group(1)} tools, real {n}"


def test_architecture_index_counts_match_registry():
    src = (REPO / "docs/architecture/index.md").read_text(encoding="utf-8")
    m = re.search(r"(\d+) registered tools", src)
    assert m, (
        "architecture/index.md ya no cita 'N registered tools' en el Layer Map "
        "— actualizar el patrón de este guard"
    )
    n = _n_tools()
    assert (
        int(m.group(1)) == n
    ), f"drift documental: index.md dice {m.group(1)} registered tools, real {n}"


def test_manual_tools_section_count_matches_registry():
    src = (REPO / "docs/MANUAL_DE_PRUEBAS.md").read_text(encoding="utf-8")
    m = re.search(r"## §1 — Tools \((\d+)\)", src)
    assert m, (
        "MANUAL_DE_PRUEBAS ya no titula la sección de tools con '## §1 — Tools (N)' "
        "— actualizar el patrón de este guard"
    )
    n = _n_tools()
    assert int(m.group(1)) == n, f"drift documental: MANUAL §1 dice {m.group(1)} tools, real {n}"
