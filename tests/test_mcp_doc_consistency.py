"""Guard anti-regresión de los drifts documentales de MCP.

La doc miente cuando nadie la revisa: el conteo de tools citado en
mcp-integration.md debe ser el real de TOOL_DEFINITIONS, y el docstring
del server no puede citar comandos que no existen (package-mode=false no
instala [project.scripts]).
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_doc_tool_count_matches_registry():
    src = (REPO / "docs/architecture/mcp-integration.md").read_text(encoding="utf-8")
    m = re.search(r"(\d+)\s+function-calling tools", src)
    assert m, (
        "mcp-integration.md ya no cita el conteo de tools con la frase "
        "'N function-calling tools' — actualizar el patrón de este guard"
    )
    from tools.specs import TOOL_DEFINITIONS

    assert int(m.group(1)) == len(TOOL_DEFINITIONS), (
        f"drift documental: la doc dice {m.group(1)} tools, "
        f"TOOL_DEFINITIONS tiene {len(TOOL_DEFINITIONS)}"
    )


def test_server_docstring_cites_no_phantom_command():
    src = (REPO / "core/mcp/server.py").read_text(encoding="utf-8")
    assert "morphix mcp-server" not in src, (
        "el docstring vuelve a citar `morphix mcp-server` — ese comando no "
        "existe (package-mode=false no instala [project.scripts])"
    )
