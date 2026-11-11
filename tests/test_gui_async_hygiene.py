# tests/test_gui_async_hygiene.py — guard estructural del puente Qt↔asyncio
"""La capa desktop jamás ejecuta asyncio.run: envenena el thread-local del
hilo Qt y las corrutinas siguientes enmudecen sobre un loop huérfano que
nadie bombea. Para valores async inmediatos existe run_async_blocking.
El escaneo es por AST: docstrings que mencionen asyncio.run() no cuentan.
"""

import ast
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent / "desktop"


def _asyncio_run_calls() -> list[str]:
    offenders: list[str] = []
    for path in sorted(DESKTOP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if ast.unparse(node.func) == "asyncio.run":
                    offenders.append(f"{path.relative_to(DESKTOP.parent)}:{node.lineno}")
    return offenders


def test_desktop_sin_asyncio_run():
    assert _asyncio_run_calls() == []
