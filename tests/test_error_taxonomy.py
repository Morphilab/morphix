# tests/test_error_taxonomy.py — todo error de dominio hereda de MorphixError
# con un code estable (agrupación en logs/telemetría).
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("core", "llm", "orchestration", "tools", "agents")

# Errores que NO son de dominio (stdlib/envivo o de terceros) viven fuera.
SUFFIX_EXCLUDES = ("test_",)


def _domain_error_classes() -> dict[str, str]:
    """{ClassName: file} de las clases *Error/*Error-like definidas en los paquetes."""
    out: dict[str, str] = {}
    for pkg in PACKAGES:
        for py in (REPO / pkg).rglob("*.py"):
            if any(py.name.startswith(s) for s in SUFFIX_EXCLUDES):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Error"):
                    out.setdefault(node.name, str(py.relative_to(REPO)))
    return out


def test_toda_clase_error_hereda_de_morphix_error():
    from core.errors import MorphixError

    clases = _domain_error_classes()
    assert len(clases) >= 8, "el escaneo AST no encontró las clases conocidas"
    huerfanos: dict[str, str] = {}
    for name, where in clases.items():
        mod = __import__(where.replace("/", ".").rsplit(".", 1)[0], fromlist=[name])
        cls = getattr(mod, name, None)
        if cls is None or not (isinstance(cls, type) and issubclass(cls, MorphixError)):
            huerfanos[name] = where
    assert (
        not huerfanos
    ), "clases de error sin la base MorphixError (sin code agrupable):\n" + "\n".join(
        f"  {k} ({v})" for k, v in sorted(huerfanos.items())
    )


def test_morphix_error_base_y_codes_estables():
    from core.bots import BotError
    from core.errors import MorphixError

    assert issubclass(MorphixError, Exception)
    assert MorphixError("x").code == "morphix"
    assert BotError("x").code == "bot"
