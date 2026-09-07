# Policy de comentarios: el guard contiene los patrones que detecta.
"""Los comentarios explican restricciones al PRÓXIMO lector — no narran la
historia del proyecto (esa vive en git, AGENTS.md y dev/reportes). Este guard
falla si reaparecen anotaciones de sesión: tags de sprint/auditoría (# C1:,
(C6/T13), RC4…), fechas sueltas, o lead-ins narrativos ("root fix", "sprint",
"deuda", "rebrand", "hoy", "merge de la rama X").

Alcance: comentarios de línea completa E inline (tokenize) y docstrings (ast)
de los paquetes de producción + tests + run.py; comentarios de línea completa
en templates/**/*.yaml y example.env. Las migraciones de alembic quedan fuera
(el boilerplate "Create Date" con fecha es estándar del generador).
Excepciones puntuales: añadir el path a _ALLOWLIST con su motivo."""

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Token de tag: letra(s) mayúsculas + dígitos, con sufijo opcional
# ("C2a", "P0.2", "B8''", "T09") — las familias con prefijo van aparte.
# El lookahead excluye continuaciones que no son tags ("E2E", "L2²", "S256").
_TAG_TOKEN = r"[A-Z]-?\d+(?:\.\w+)?[a-z]*(?:''|')?(?![A-Z0-9²³])"
_TAG_FAMILIES = (
    "RC-?[A-Z0-9]+",  # RC1, RC4, RC-Botmode
    "SEC-[A-Z]\\d?",
    "A-I[IVX]*(?:\\.[a-z])?",  # A-I, A-II, A-I.c
    "A-X[IVX]*",  # A-X, A-XI, A-XIII
    "BOT-?[A-Z0-9]+",  # BOT-X3, BOT2-C1, BOT-M
    "DSH-?[A-Z0-9]+",  # DSH-H1, DSH2-M6
    "INT-M\\d+",
    "NV-[A-Z]\\d+",
    "ORCH-[A-Z]\\d+",
    "ID-[A-Z]+-\\d+",  # ID-BOT-10
)
_TOKEN = rf"(?:{'|'.join(_TAG_FAMILIES)}|{_TAG_TOKEN})"
# Cadena de tags: "C6/T13", "C11/C12", "A-II + M5-MCP"
_CHAIN = rf"{_TOKEN}(?:\s*[/+]\s*{_TOKEN})*"
# Tag como etiqueta (seguida de : ( — -) o envuelta en paréntesis "(T15)"
TAG_LABEL = re.compile(rf"(?:\([^()\n]*\b{_TOKEN}[^()\n]*\)|{_CHAIN}\s*[:：(—-])")
# Narración de sesión en cualquier parte del comentario/docstring
SESSION_NARRATION = re.compile(
    r"\b(auditor[íi]a|root\s+fix|rebrand(?:ing)?\b|deuda\s+\w|sprint\s+\w|"
    r"\b20\d\d-\d\d-\d\d\b|[Ff]ase\s+\d|"
    r"merges?\s+(?:de|del|con|from)?\s*(?:la\s+)?(?:rama|branch)\b|"
    r"merge\s+[0-9a-f]{7}\b|"
    r"rama\s+(?:fix|feat|chore|refactor|bug|release|main|public)\b|"
    r"\bhoy\b|\bayer\b|"
    r"fix\s?\d|post-fix|pre-fix|desacople)",
    re.IGNORECASE,
)

_PY_PACKAGES = ("core", "llm", "agents", "tools", "orchestration", "desktop", "viewer", "tests")
_PY_EXTRA = ("run.py",)
# No-.py: solo comentarios de línea completa (los .yaml/.env no se tokenizan)
_NONPY_FILES = ("example.env",)
_NONPY_GLOBS = (("templates", "*.yaml"),)
_SELF = str(Path(__file__).relative_to(ROOT))

# (path, motivo) — archivos que el guard no escanea
_ALLOWLIST: dict[str, str] = {
    _SELF: "el guard mismo contiene los patrones que detecta",
}


def _targets() -> list[Path]:
    files: list[Path] = []
    for pkg in _PY_PACKAGES:
        files.extend((ROOT / pkg).rglob("*.py"))
    files.extend(ROOT / extra for extra in _PY_EXTRA)
    files.extend(ROOT / name for name in _NONPY_FILES)
    for base, pattern in _NONPY_GLOBS:
        files.extend((ROOT / base).rglob(pattern))
    return sorted(f for f in files if f.exists() and "__pycache__" not in f.parts)


def _comentarios_py(text: str):
    """Todos los comentarios reales del código (línea completa e inline)."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                body = tok.string.lstrip("#").strip()
                if body.startswith(("noqa", "type:")):
                    continue
                out.append((tok.start[0], tok.string))
    except tokenize.TokenError:
        pass
    return out


def _docstrings(text: str) -> list[tuple[int, str]]:
    """(lineno, docstring) de módulo/clase/función."""
    out: list[tuple[int, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.append((getattr(node, "lineno", 1), doc))
    return out


def _comentarios_de_linea(text: str):
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            yield lineno, stripped


def _scan() -> list[str]:
    violations: list[str] = []
    for f in _targets():
        rel = str(f.relative_to(ROOT))
        if rel in _ALLOWLIST:
            continue
        text = f.read_text(encoding="utf-8")
        es_py = f.suffix == ".py"
        hallazgos = _comentarios_py(text) if es_py else list(_comentarios_de_linea(text))
        for lineno, fragmento in hallazgos:
            if TAG_LABEL.search(fragmento) or SESSION_NARRATION.search(fragmento):
                violations.append(f"{rel}:{lineno}: {fragmento.strip()[:120]}")
        if es_py:
            for lineno, doc in _docstrings(text):
                for offset, line in enumerate(doc.splitlines(), 1):
                    if TAG_LABEL.search(line) or SESSION_NARRATION.search(line):
                        violations.append(f"{rel}:{lineno}+{offset}: {line.strip()[:120]}")
    return violations


def test_sin_anotaciones_de_sesion_en_comentarios_ni_docstrings():
    violations = _scan()
    assert not violations, (
        "Comentarios/docstrings con anotaciones de sesión (tags de sprint/auditoría,\n"
        "fechas, narración de fixes). La convención: comentarios = restricciones\n"
        "atemporales para el próximo lector; la historia vive en git/AGENTS.md/dev/reportes.\n"
        + "\n".join(violations[:40])
        + (f"\n… y {len(violations) - 40} más" if len(violations) > 40 else "")
    )


def test_allowlist_solo_contiene_paths_existentes():
    for rel in _ALLOWLIST:
        assert (ROOT / rel).exists(), f"allowlist apunta a un archivo inexistente: {rel}"
