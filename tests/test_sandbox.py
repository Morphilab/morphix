# tests/test_sandbox.py
import pytest

from tools.code_execution import CodeExecutor


@pytest.mark.asyncio
async def test_execute_safe_print():
    """print básico funciona en sandbox."""
    result = await CodeExecutor.execute("print('hello sandbox')")
    assert "hello sandbox" in result.get("text", "")


@pytest.mark.asyncio
async def test_execute_math_operations():
    """Módulo math permitido en sandbox."""
    result = await CodeExecutor.execute("import math; print(math.sqrt(16))")
    assert "4.0" in result.get("text", "")


@pytest.mark.asyncio
async def test_execute_blocked_import():
    """Import de módulo bloqueado produce error."""
    result = await CodeExecutor.execute("import os; print('bad')")
    assert any(
        phrase in result.get("text", "") for phrase in ["blocked", "not allowed", "security"]
    ), f"Expected blocked import message, got: {result.get('text', '')}"


@pytest.mark.asyncio
async def test_execute_syntax_error():
    """Error de sintaxis se captura."""
    result = await CodeExecutor.execute("print(])")
    assert "Error" in result.get("text", "") or "error" in result.get("text", "").lower()


@pytest.mark.asyncio
async def test_execute_empty_code():
    """Código vacío se ejecuta sin error."""
    result = await CodeExecutor.execute("")
    assert result is not None


@pytest.mark.asyncio
async def test_execute_numpy_operations():
    """numpy está permitido en sandbox."""
    result = await CodeExecutor.execute("import numpy as np; print(np.array([1,2,3]))")
    assert "[1 2 3]" in result.get("text", "")


@pytest.mark.asyncio
async def test_execute_json_module():
    """json está en módulos seguros."""
    result = await CodeExecutor.execute('import json; print(json.dumps({"a":1}))')
    assert '"a"' in result.get("text", "")


@pytest.mark.asyncio
async def test_execute_last_expression_value():
    """La última expresión devuelve su valor aunque no se haga print (estilo REPL)."""
    result = await CodeExecutor.execute("a = 2\nb = 3\na + b")
    assert result.get("success") is True
    assert result.get("text", "").strip() == "5"


@pytest.mark.asyncio
async def test_execute_no_output_without_expression():
    """Sin print ni expresión final → mensaje de 'no output'."""
    result = await CodeExecutor.execute("x = 5")
    assert result.get("success") is True
    assert "no output" in result.get("text", "").lower()


@pytest.mark.asyncio
async def test_execute_print_preferred_over_expression():
    """Si hay print, se usa esa salida (no se pisa con el valor de la expresión)."""
    result = await CodeExecutor.execute("print('hola')\n42")
    assert "hola" in result.get("text", "")


# ==================== SAFE_MODULES restriction tests ====================


@pytest.mark.asyncio
async def test_safe_ast_parse_allowed():
    """ast.parse está permitido en sandbox."""
    result = await CodeExecutor.execute("import ast\nprint(type(ast.parse('x = 1')))")
    assert result.get("success") is True
    assert "ast.Module" in result.get("text", "")


@pytest.mark.asyncio
async def test_safe_ast_walk_blocked():
    """ast.walk no está disponible en sandbox (SimpleNamespace)."""
    result = await CodeExecutor.execute("import ast\nast.walk(None)\nprint('should not reach')")
    assert result.get("success") is False


@pytest.mark.asyncio
async def test_safe_sqlite3_memory_allowed():
    """sqlite3 con :memory: funciona en sandbox."""
    result = await CodeExecutor.execute(
        "import sqlite3\nconn = sqlite3.connect(':memory:')\n"
        "conn.execute('CREATE TABLE t(x)')\n"
        "conn.execute('INSERT INTO t VALUES(42)')\n"
        "rows = conn.execute('SELECT * FROM t').fetchall()\n"
        "print(rows)\nconn.close()"
    )
    assert result.get("success") is True
    assert "42" in result.get("text", "")


@pytest.mark.asyncio
async def test_safe_sqlite3_file_blocked():
    """sqlite3.connect a archivo físico lanza PermissionError."""
    result = await CodeExecutor.execute('import sqlite3\nconn = sqlite3.connect("test.db")')
    assert result.get("success") is False
    assert (
        "memory" in result.get("text", "").lower() or "permission" in result.get("text", "").lower()
    )


@pytest.mark.asyncio
async def test_safe_io_stringio_allowed():
    """io.StringIO está permitido en sandbox."""
    result = await CodeExecutor.execute(
        "import io\nbuf = io.StringIO()\nbuf.write('hello')\nbuf.seek(0)\nprint(buf.read())"
    )
    assert result.get("success") is True
    assert "hello" in result.get("text", "")


@pytest.mark.asyncio
async def test_safe_io_bytesio_blocked():
    """io.BytesIO no está disponible en sandbox."""
    result = await CodeExecutor.execute("import io\nbuf = io.BytesIO()\nprint('should not reach')")
    assert result.get("success") is False
