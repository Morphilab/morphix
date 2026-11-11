# tests/test_context_contract.py
"""El constructor de los contenedores de orchestration es un contrato.

Remover un campo de WorkflowContext/Session/WorkflowEvents deja vivos a los
callers que aún lo pasan, y el fallo solo aparece en la ruta de runtime que los
ejecuta: mypy no chequea cuerpos sin tipos y la suite no ejercita cada ruta
GUI. Este guard escanea por AST todos los call sites de los paquetes de
producto y verifica cada keyword contra los campos reales del dataclass.
"""

import ast
from pathlib import Path

from orchestration.context import Session, WorkflowContext, WorkflowEvents

_BASE = Path(__file__).resolve().parent.parent
_ROOTS = ("core", "llm", "agents", "tools", "orchestration", "desktop", "viewer", "tests")
_CONTRACTS = {
    "WorkflowContext": WorkflowContext,
    "Session": Session,
    "WorkflowEvents": WorkflowEvents,
}


def _bound_contracts(tree: ast.Module) -> dict[str, str]:
    """Nombres locales que apuntan a los dataclasses del contexto.

    Se resuelve por import para no confundir clases homónimas de terceros
    (p. ej. un Session de otra librería) con las nuestras.
    """
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "orchestration.context":
            for alias in node.names:
                if alias.name in _CONTRACTS:
                    names[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "orchestration.context":
                    names[alias.asname or "orchestration"] = ""
    return names


def _offenders_for(path: Path, tree: ast.Module) -> list[str]:
    bound = _bound_contracts(tree)
    if not bound:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        contract = None
        if isinstance(fn, ast.Name):
            contract = bound.get(fn.id)
        elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            # uso por módulo: orchestration.context.WorkflowContext(...)
            if bound.get(fn.value.id) == "" and fn.attr in _CONTRACTS:
                contract = fn.attr
        if not contract:
            continue
        fields = set(_CONTRACTS[contract].__dataclass_fields__)
        for kw in node.keywords:
            if kw.arg and kw.arg not in fields:
                rel = path.relative_to(_BASE)
                found.append(f"{rel}:{node.lineno} — {contract}({kw.arg}=...) no es campo")
    return found


def test_callers_usan_solo_campos_de_los_contratos():
    offenders: list[str] = []
    for root_name in _ROOTS:
        root = _BASE / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            offenders.extend(_offenders_for(path, tree))

    assert not offenders, (
        "Callers con kwargs fuera del contrato de orchestration.context "
        "(un campo removido con caller vivo revienta en runtime):\n" + "\n".join(offenders)
    )
