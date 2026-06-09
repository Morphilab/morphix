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
import time
import traceback
from io import StringIO

import matplotlib

matplotlib.use("Agg")  # Backend no interactivo

import matplotlib.pyplot as plt
import numpy as np
from RestrictedPython import limited_builtins, safe_globals
from RestrictedPython.Eval import default_guarded_getattr
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
)

logger = logging.getLogger(__name__)

from core.path_resolver import paths

OUTPUT_DIR = paths.charts_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ==================== ALLOWED MODULES AND BUILTINS (VERY STRICT) ====================
SAFE_MODULES = {
    "math": __import__("math"),
    "random": __import__("random"),
    "collections": __import__("collections"),
    "datetime": __import__("datetime"),
    "re": __import__("re"),
    "json": __import__("json"),
    "sqlite3": __import__("sqlite3"),
    "ast": __import__("ast"),
    "io": __import__("io"),
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

        try:
            restricted_globals = safe_globals.copy()
            restricted_globals.update(
                {
                    "__builtins__": {
                        **limited_builtins,
                        **SAFE_BUILTINS,
                        "print": _sandbox_print,
                        "__import__": safe_import,
                        "_getattr_": default_guarded_getattr,
                        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
                        "_unpack_sequence_": guarded_unpack_sequence,
                    },
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
                exec(compile(tree, "<inline>", "exec"), restricted_globals)
                if last_expr is not None:
                    value = eval(compile(last_expr, "<inline>", "eval"), restricted_globals)
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
            msg = f"❌ Syntax error:\nLine {e.lineno}: {e.msg}"
            logger.error(f"SyntaxError: {e}")
            return {"text": msg, "success": False}
        except Exception as e:
            error_type = type(e).__name__
            msg = f"❌ Execution error: {error_type}\n{str(e)}"
            logger.error(f"Execution error:\n{traceback.format_exc()}")
            return {"text": msg, "success": False}


# Instancia global
restricted_executor = RestrictedExecutor()
