# core/sandbox/runner.py — hijo confinado del sandbox
"""Ejecuta código del modelo bajo RestrictedPython en un PROCESO SEPARADO
del host. Es el endpoint ``python runner.py <charts_dir> <mem_mb>`` que el
supervisor (core/sandbox/restricted_executor.py) lanza con env allowlist.

Protocolo:
    stdin : código fuente (UTF-8)
    stdout: UNA línea JSON {"success": bool, "text": str, "image_path": str|null}
    exit  : 0 con envelope; 3 ante crash del bootstrap (host muestra genérico)

Garantías (desde aquí, child-scoped — el host jamás aloja código del modelo):
- RLIMIT_AS (mem_mb) se aplica DESPUÉS de los imports confiables y ANTES del
  código del usuario; el cap rompe solo asignaciones DEL HIJO.
- Timeout: lo impone el supervisor con SIGKILL (cualquier C-loop del usuario
  muere con el proceso; no hay threads zombis en nadie).
- Imports SOLO stdlib permitida + RestrictedPython + numpy + matplotlib.
  JAMÁS importa core.* — el hijo no carga settings/.env/secretos del host.
- El fence de savefig acota escrituras a charts_dir.
"""

import ast
import io as _io
import json
import operator
import sqlite3 as _sqlite3
import sys
import types
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")  # sin display; el hijo jamás abre ventanas
import matplotlib.pyplot as plt
from RestrictedPython import (
    compile_restricted,
    limited_builtins,
    safe_builtins,
    safe_globals,
)
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
)

SANDBOX_OUTPUT_CAP = 64 * 1024
_CHARTS_DIR = ""


def _cap_output(text: str) -> str:
    if len(text) > SANDBOX_OUTPUT_CAP:
        return (
            text[:SANDBOX_OUTPUT_CAP]
            + f"\n…[salida truncada: {len(text)} → {SANDBOX_OUTPUT_CAP} chars]"
        )
    return text


def _apply_child_memory_cap(mem_mb: int) -> None:
    """RLIMIT_AS child-scoped: sin hilos ajenos, sin ventana de carrera, sin
    VmSize-guard (el único proceso afectado es este hijo efímero)."""
    if mem_mb <= 0:
        return
    import resource

    old = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (int(mem_mb) * 1024 * 1024, old[1]))


def _restore_memory_limits() -> None:
    """Suelta el cap ANTES de componer el envelope: un MemoryError del código
    del usuario no debe romper también el camino de reporte del hijo."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, resource.getrlimit(resource.RLIMIT_AS))
    except Exception:
        pass


# ==================== SAFE MODULE WRAPPERS (paridad con el executor previo) ====================


class _SafeConnection:
    """Proxy con API de sqlite EN LISTA BLANCA (sin extensiones ni ejecución de código)."""

    _ALLOWED_CONN = {
        "cursor",
        "commit",
        "rollback",
        "close",
        "execute",
        "executemany",
        "executescript",
        "in_transaction",
        "total_changes",
    }

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ALLOWED_CONN:
            raise AttributeError(f"sqlite3.Connection.{name} bloqueado en sandbox")
        attr = getattr(self._conn, name)

        def _wrapped(*args, **kwargs):
            result = attr(*args, **kwargs)
            if isinstance(result, _sqlite3.Cursor):
                return _SafeCursor(result)
            return result

        return _wrapped

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)


class _SafeCursor:
    _ALLOWED_CUR = {
        "execute",
        "executemany",
        "fetchone",
        "fetchall",
        "fetchmany",
        "close",
        "rowcount",
        "lastrowid",
        "description",
        "arraysize",
        "setinputsizes",
        "setoutputsize",
    }

    def __init__(self, cur):
        self._cur = cur

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ALLOWED_CUR:
            raise AttributeError(f"sqlite3.Cursor.{name} bloqueado en sandbox")
        return getattr(self._cur, name)


class _SafeSQLite3:
    PARSE_DECLTYPES = _sqlite3.PARSE_DECLTYPES
    Row = _sqlite3.Row
    sqlite_version = _sqlite3.sqlite_version

    def connect(self, database=":memory:", **kwargs):
        if database != ":memory:":
            raise PermissionError("sqlite3: only :memory: databases are allowed in sandbox")
        return _SafeConnection(_sqlite3.connect(":memory:", **kwargs))


class _SafeIO:
    StringIO = _io.StringIO


class _SafePyplot:
    """Proxy read-only de pyplot + fence de savefig bajo charts/."""

    _ALLOWED = frozenset(
        {
            "figure",
            "plot",
            "scatter",
            "bar",
            "barh",
            "hist",
            "pie",
            "stackplot",
            "stem",
            "errorbar",
            "fill_between",
            "imshow",
            "title",
            "xlabel",
            "ylabel",
            "xlim",
            "ylim",
            "legend",
            "grid",
            "text",
            "annotate",
            "subplot",
            "subplots",
            "tight_layout",
            "gca",
            "gcf",
            "clf",
            "cla",
            "sca",
            "savefig",
            "close",
            "show",
        }
    )

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ALLOWED:
            raise AttributeError(f"matplotlib.pyplot.{name} no está disponible en el sandbox")
        if name == "savefig":
            # plt.savefig real = escritura arbitraria.
            # Acotado a charts/; ruta absoluta ajena ⇒ PermissionError.
            return _SafePyplot._fenced_savefig
        return getattr(plt, name)

    @staticmethod
    def _fenced_savefig(*args, **kwargs):
        from pathlib import Path

        charts = Path(_CHARTS_DIR).resolve()
        fname = args[0] if args else None
        if fname is None:
            fname = charts / f"plot_{int(__import__('time').time())}.png"
        target = Path(str(fname)).expanduser()
        if not target.is_absolute():
            target = charts / target
        resolved = target.resolve(strict=False)
        if resolved != charts and charts not in resolved.parents:
            raise PermissionError(
                f"savefig solo puede escribir bajo {charts} (sandbox); "
                f"ruta rechazada: {resolved}"
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return plt.savefig(str(resolved), *args[1:], **kwargs)


class _SafeNumpyLinalg:
    _ALLOWED = frozenset(
        {
            "matrix_power",
            "norm",
            "det",
            "slogdet",
            "inv",
            "solve",
            "tensorsolve",
            "tensorinv",
            "lstsq",
            "pinsv",
            "pinv",
            "matrix_rank",
            "cond",
            "trace",
        }
    )

    def __init__(self, mod):
        self._mod = mod

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ALLOWED:
            raise PermissionError(f"numpy.linalg.{name} bloqueado en el sandbox")
        return getattr(self._mod, name)


class _SafeNumpyRandom:
    _ALLOWED = frozenset(
        {
            "seed",
            "rand",
            "randn",
            "randint",
            "random",
            "uniform",
            "normal",
            "choice",
            "shuffle",
            "permutation",
        }
    )

    def __init__(self, mod):
        self._mod = mod

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ALLOWED:
            raise PermissionError(f"numpy.random.{name} bloqueado en el sandbox")
        return getattr(self._mod, name)


class _SafeNumpy:
    """Proxy de numpy con API matemática pública en lista blanca."""

    _ALLOWED = frozenset(
        {
            # creación
            "array",
            "asarray",
            "zeros",
            "ones",
            "full",
            "empty",
            "arange",
            "linspace",
            "logspace",
            "eye",
            "identity",
            "diag",
            "meshgrid",
            # manipulación
            "concatenate",
            "stack",
            "vstack",
            "hstack",
            "column_stack",
            "expand_dims",
            "squeeze",
            "transpose",
            "tile",
            "repeat",
            "flip",
            "flipud",
            "fliplr",
            "roll",
            # reducciones
            "sum",
            "prod",
            "mean",
            "average",
            "std",
            "var",
            "median",
            "percentile",
            "quantile",
            "min",
            "max",
            "amin",
            "amax",
            "ptp",
            "argmin",
            "argmax",
            "cumsum",
            "cumprod",
            "all",
            "any",
            "count_nonzero",
            "unique",
            "trace",
            "corrcoef",
            "cov",
            # ufuncs / matemática
            "abs",
            "absolute",
            "sqrt",
            "exp",
            "expm1",
            "log",
            "log2",
            "log10",
            "log1p",
            "sin",
            "cos",
            "tan",
            "arcsin",
            "arccos",
            "arctan",
            "arctan2",
            "sinh",
            "cosh",
            "tanh",
            "degrees",
            "radians",
            "floor",
            "ceil",
            "trunc",
            "around",
            "round_",
            "sign",
            "clip",
            "maximum",
            "minimum",
            "fmax",
            "fmin",
            "hypot",
            "power",
            "mod",
            "remainder",
            "add",
            "subtract",
            "multiply",
            "divide",
            "true_divide",
            "floor_divide",
            "dot",
            "vdot",
            "matmul",
            "inner",
            "outer",
            "kron",
            "cross",
            # lógica / comparación
            "where",
            "nonzero",
            "isnan",
            "isinf",
            "isfinite",
            "nan_to_num",
            "isclose",
            "allclose",
            "array_equal",
            "diff",
            "sort",
            "argsort",
            # constantes y tipos escalares comunes
            "pi",
            "e",
            "euler_gamma",
            "inf",
            "nan",
            "newaxis",
            "float16",
            "float32",
            "float64",
            "int8",
            "int16",
            "int32",
            "int64",
            "uint8",
            "uint16",
            "uint32",
            "uint64",
            "complex64",
            "complex128",
            "bool_",
            "str_",
            # submódulos proxy
            "linalg",
            "random",
        }
    )

    def __init__(self, mod):
        self._mod = mod
        self._linalg = _SafeNumpyLinalg(mod.linalg)
        self._random_mod = _SafeNumpyRandom(mod.random)

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ALLOWED:
            raise PermissionError(f"numpy.{name} bloqueado en el sandbox")
        if name == "linalg":
            return self._linalg
        if name == "random":
            return self._random_mod
        return getattr(self._mod, name)


SAFE_MODULES: dict[str, Any] = {
    "math": __import__("math"),
    "random": __import__("random"),
    "collections": __import__("collections"),
    "datetime": __import__("datetime"),
    "re": __import__("re"),
    "json": __import__("json"),
    "sqlite3": _SafeSQLite3(),
    "ast": types.SimpleNamespace(parse=ast.parse),
    "io": _SafeIO(),
    "numpy": _SafeNumpy(np),
    "np": _SafeNumpy(np),
    "plt": _SafePyplot(),
}

SAFE_BUILTINS = {
    "sum": sum,
    "len": len,
    "max": max,
    "min": min,
    "abs": abs,
    "round": round,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "sorted": sorted,
    "reversed": reversed,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "repr": repr,
    "type": type,
    "isinstance": isinstance,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "ZeroDivisionError": ZeroDivisionError,
    "FileNotFoundError": FileNotFoundError,
}

_BLOCKED_IMPORTS = (
    "os",
    "sys",
    "shutil",
    "subprocess",
    "socket",
    "requests",
    "pathlib",
    "pickle",
    "builtins",
)


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import extremadamente restrictivo (paridad con el executor previo)."""
    if name in SAFE_MODULES:
        return SAFE_MODULES[name]
    if name in _BLOCKED_IMPORTS:
        raise ImportError(f"Import blocked for security: {name}")
    raise ImportError(f"Import not allowed: {name}")


_INPLACE_OPS = {
    "+=": operator.iadd,
    "-=": operator.isub,
    "*=": operator.imul,
    "/=": operator.itruediv,
    "//=": operator.ifloordiv,
    "%=": operator.imod,
    "**=": operator.ipow,
    "<<=": operator.ilshift,
    ">>=": operator.irshift,
    "&=": operator.iand,
    "|=": operator.ior,
    "^=": operator.ixor,
    "@=": operator.imatmul,
}


def _inplacevar_(op: str, x, y):
    return _INPLACE_OPS[op](x, y)


def _apply_(func, *args, **kwargs):
    return func(*args, **kwargs)


def _make_print_collector(output_buffer):
    class _PrintCollector:
        def __init__(self, _getattr_=None):
            self._getattr_: Any = _getattr_

        def write(self, text):
            output_buffer.write(text)

        def _call_print(self, *objects, **kwargs):
            if kwargs.get("file", None) is None:
                kwargs["file"] = self
            else:
                self._getattr_(kwargs["file"], "write")
            print(*objects, **kwargs)

    return _PrintCollector


def _rewrite_name_nodes(tree) -> None:
    class _NameRewriter(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id == "__name__" and isinstance(node.ctx, ast.Load):
                return ast.copy_location(ast.Constant("__main__"), node)
            return node

    _NameRewriter().visit(tree)


def _format_syntax_error(e: SyntaxError) -> str:
    if isinstance(e.msg, (list, tuple)) and e.msg:  # type: ignore[attr-defined]
        detail = str(e.msg[0])
    elif e.msg and e.lineno is not None:
        detail = str(e.msg)  # type: ignore[attr-defined]
    else:
        detail = str(e.msg or e)  # type: ignore[attr-defined]
    return f"❌ Syntax error:\n{detail}"


def main() -> int:
    global _CHARTS_DIR
    if len(sys.argv) < 3:
        print("usage: runner.py <charts_dir> <mem_mb>", file=sys.stderr)
        return 3
    _CHARTS_DIR = sys.argv[1]
    try:
        mem_mb = int(sys.argv[2])
    except ValueError:
        mem_mb = 0
    code = sys.stdin.read()

    output_buffer = _io.StringIO()

    def _sandbox_print(*args, **kwargs):
        print(*args, **{k: v for k, v in kwargs.items() if k != "file"}, file=output_buffer)

    _print_collector_cls = _make_print_collector(output_buffer)
    _print_guard = _print_collector_cls(safe_builtins["_getattr_"])

    try:
        restricted_globals = safe_globals.copy()
        restricted_globals.update(
            {
                "__name__": "__main__",
                "__builtins__": {
                    **limited_builtins,
                    **SAFE_BUILTINS,
                    "print": _sandbox_print,
                    "__import__": safe_import,
                },
                "_getattr_": safe_builtins["_getattr_"],
                "_getitem_": default_guarded_getitem,
                "_getiter_": default_guarded_getiter,
                "_write_": full_write_guard,
                "_unpack_sequence_": guarded_unpack_sequence,
                "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
                "_inplacevar_": _inplacevar_,
                "_apply_": _apply_,
                "_print_": _print_collector_cls,
                "_print": _print_guard,
                **SAFE_MODULES,
            }
        )
    except Exception as e:  # bootstrap crash
        print(f"runner bootstrap: {e}", file=sys.stderr)
        return 3

    last_error: BaseException | None = None
    last_value: str | None = None
    try:
        _apply_child_memory_cap(mem_mb)
    except Exception:
        pass  # sin cap: el timeout del supervisor sigue siendo la barrera

    try:
        tree = ast.parse(code, "<inline>", "exec")
        last_expr = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_stmt = tree.body.pop()
            assert isinstance(last_stmt, ast.Expr)  # narrow para mypy
            last_expr = ast.Expression(last_stmt.value)
            ast.fix_missing_locations(last_expr)
        _rewrite_name_nodes(tree)
        exec(compile_restricted(tree, "<inline>", "exec"), restricted_globals)  # noqa: S102
        if last_expr is not None:
            _rewrite_name_nodes(last_expr)
            value = eval(  # noqa: S307
                compile_restricted(ast.unparse(last_expr), "<inline>", "eval"),
                restricted_globals,
            )
            if value is not None:
                last_value = repr(value)
    except SyntaxError as e:
        _restore_memory_limits()
        envelope = {"success": False, "text": _format_syntax_error(e), "image_path": None}
        print(json.dumps(envelope, ensure_ascii=False))
        return 0
    except BaseException as e:  # noqa: BLE001 — se reporta en el envelope
        last_error = e
    finally:
        _restore_memory_limits()

    if last_error is not None:
        error_type = type(last_error).__name__
        msg = _cap_output(f"❌ Execution error: {error_type}\n{str(last_error)}")
        envelope = {"success": False, "text": msg, "image_path": None}
        print(json.dumps(envelope, ensure_ascii=False))
        return 0

    if last_value is not None:
        last_value = _cap_output(last_value)
    captured = _cap_output(output_buffer.getvalue().strip())
    if not captured and last_value is not None:
        captured = last_value

    # Auto-chart: figuras vivas al terminar → PNG bajo charts/
    image_path = None
    if plt.get_fignums():
        import time as _time

        image_path = str(__import__("pathlib").Path(_CHARTS_DIR) / f"plot_{int(_time.time())}.png")
        plt.savefig(image_path, dpi=200, bbox_inches="tight")
        plt.close("all")
        captured += f"\n\n![Chart generated]({image_path})"

    result_text = captured or "✅ Code executed successfully (no output)."
    envelope = {"success": True, "text": result_text, "image_path": image_path}
    print(json.dumps(envelope, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
