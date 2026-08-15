# core/sandbox/restricted_executor.py
"""
RestrictedPython Sandbox — Hardened version
- Timeout de ejecución
- Guards extremadamente estrictos
- Limitación fuerte de imports y builtins
- Mejor manejo de errores y mensajes amigables
"""

import ast
import asyncio
import logging
import operator
import time
import traceback
import types
from io import StringIO
from typing import Any

import matplotlib

matplotlib.use("Agg")  # Backend no interactivo

import matplotlib.pyplot as plt
import numpy as np
from RestrictedPython import compile_restricted, limited_builtins, safe_globals
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safe_builtins,
)

logger = logging.getLogger(__name__)

from core.path_resolver import paths

OUTPUT_DIR = paths.charts_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ==================== SAFE MODULE WRAPPERS ====================

import sqlite3 as _sqlite3


class _SafeSQLite3:
    """sqlite3 wrapper that only allows :memory: databases."""

    def __init__(self):
        self.PARSE_DECLTYPES = _sqlite3.PARSE_DECLTYPES
        self.Row = _sqlite3.Row
        self.sqlite_version = _sqlite3.sqlite_version

    def connect(self, database=":memory:", **kwargs):
        if database != ":memory:":
            raise PermissionError("sqlite3: only :memory: databases are allowed in sandbox")
        return _sqlite3.connect(":memory:", **kwargs)


import io as _io


class _SafeIO:
    """io wrapper that only allows StringIO."""

    StringIO = _io.StringIO


# ==================== ALLOWED MODULES AND BUILTINS (VERY STRICT) ====================
SAFE_MODULES = {
    "math": __import__("math"),
    "random": __import__("random"),
    "collections": __import__("collections"),
    "datetime": __import__("datetime"),
    "re": __import__("re"),
    "json": __import__("json"),
    "sqlite3": _SafeSQLite3(),
    "ast": types.SimpleNamespace(parse=ast.parse),
    "io": _SafeIO(),
    "numpy": np,
    "np": np,
    "plt": plt,
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
    # Debugging / type introspection (pure read-only, zero risk)
    "repr": repr,
    "type": type,
    "isinstance": isinstance,
    # Standard exception types — needed for try/except blocks
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "ZeroDivisionError": ZeroDivisionError,
    "FileNotFoundError": FileNotFoundError,
}


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import extremadamente restrictivo"""
    if name in SAFE_MODULES:
        return SAFE_MODULES[name]
    if name in (
        "os",
        "sys",
        "shutil",
        "subprocess",
        "socket",
        "requests",
        "pathlib",
        "pickle",
        "builtins",
    ):
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
    """Guarda las asignaciones aumentadas (`n += 1`) que genera RestrictedPython."""
    return _INPLACE_OPS[op](x, y)


def _apply_(func, *args, **kwargs):
    """Guarda las llamadas con *args/**kwargs que genera RestrictedPython."""
    return func(*args, **kwargs)


def _make_print_collector(output_buffer):
    """PrintCollector que escribe directo al buffer compartido del sandbox."""

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
    """Reemplaza las cargas de `__name__` por la constante '__main__'.

    RestrictedPython prohíbe leer nombres que empiezan por '_', pero el
    guard `if __name__ == '__main__':` es legítimo en el sandbox (donde
    `__name__` siempre vale '__main__').
    """

    class _NameRewriter(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id == "__name__" and isinstance(node.ctx, ast.Load):
                return ast.copy_location(ast.Constant("__main__"), node)
            return node

    _NameRewriter().visit(tree)


class RestrictedExecutor:
    @staticmethod
    async def execute(code: str, timeout: int = 10) -> dict:
        """Execute safely with timeout and strict guards."""
        from core.config import settings

        if not settings.allow_code_execution:
            return {
                "success": False,
                "error": "code_execution_disabled",
                "output": "Code execution disabled by system configuration.",
            }

        output_buffer = StringIO()

        # Custom print that captures to buffer
        def _sandbox_print(*args, **kwargs):
            print(
                *args,
                **{k: v for k, v in kwargs.items() if k != "file"},
                file=output_buffer,
            )

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

            # Execute the body and, if the last statement is an expression,
            # return its value (REPL style) in addition to what print() captured.
            def _run() -> str | None:
                tree = ast.parse(code, "<inline>", "exec")
                last_expr = None
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last_stmt = tree.body.pop()
                    assert isinstance(last_stmt, ast.Expr)  # narrow para mypy
                    last_expr = ast.Expression(last_stmt.value)
                    ast.fix_missing_locations(last_expr)
                _rewrite_name_nodes(tree)
                exec(compile_restricted(tree, "<inline>", "exec"), restricted_globals)
                if last_expr is not None:
                    _rewrite_name_nodes(last_expr)
                    value = eval(
                        compile_restricted(ast.unparse(last_expr), "<inline>", "eval"),
                        restricted_globals,
                    )
                    if value is not None:
                        return repr(value)
                return None

            last_value = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)

            captured = output_buffer.getvalue().strip()
            if not captured and last_value is not None:
                captured = last_value

            # Handle matplotlib plots
            image_path = None
            if plt.get_fignums():
                timestamp = int(time.time())
                image_path = str(OUTPUT_DIR / f"plot_{timestamp}.png")
                plt.savefig(image_path, dpi=200, bbox_inches="tight")
                plt.close("all")
                captured += f"\n\n![Chart generated]({image_path})"

            result_text = captured or "✅ Code executed successfully (no output)."

            return {"text": result_text, "image_path": image_path, "success": True}

        except TimeoutError:
            logger.warning("Code execution timeout (10 seconds)")
            return {
                "text": "❌ Execution time exceeded (max 10 seconds). Possible infinite loop.",
                "success": False,
            }
        except SyntaxError as e:
            if isinstance(e.msg, (list, tuple)) and e.msg:
                detail = str(e.msg[0])
            elif e.msg and e.lineno is not None:
                detail = f"Line {e.lineno}: {e.msg}"
            else:
                detail = str(e.msg or e)
            msg = f"❌ Syntax error:\n{detail}"
            logger.error(f"SyntaxError: {e}")
            return {"text": msg, "success": False}
        except Exception as e:
            error_type = type(e).__name__
            msg = f"❌ Execution error: {error_type}\n{str(e)}"
            logger.error(f"Execution error:\n{traceback.format_exc()}")
            return {"text": msg, "success": False}


# Instancia global
restricted_executor = RestrictedExecutor()
