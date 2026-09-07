"""Hardening del cliente/servidor MCP.

- stderr=PIPE sin drenar → deadlock con servidores verbosos.
- readline con límite default 64KB → ValueError mata _read_loop.
- Alias nativo corto pisa built-ins si MCP registra primero (orden-dependiente).
- Timeout de request fijo 30s vs 120s de safe_tool_call.
- Server sin allowlist de tools.
"""

import asyncio
import contextlib
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from core.mcp.client import MCPClient
from core.mcp.config import MCPServerConfig


@pytest.fixture(autouse=True)
def _restore_global_tool_state():
    """Los tests MCP mutan TOOL_DEFINITIONS/tools_registry/_SPEC_OWNERS
    globales — snapshot/restore para no contaminar el resto de la sesión."""
    from core.mcp.client import _SPEC_OWNERS
    from tools.registry import tools_registry
    from tools.specs import TOOL_DEFINITIONS as _DEFS

    defs_snapshot = dict(_DEFS)
    reg_snapshot = dict(tools_registry._tools)
    yield
    _DEFS.clear()
    _DEFS.update(defs_snapshot)
    tools_registry._tools.clear()
    tools_registry._tools.update(reg_snapshot)
    _SPEC_OWNERS.clear()


FAKE_MCP_SERVER = r"""
import sys, json, time

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

# Ruido continuo por stderr (drenaje obligatorio para no bloquear)
import threading
def noise():
    while True:
        try:
            sys.stderr.write("ruido " * 200 + "\n")
            sys.stderr.flush()
        except Exception:
            return
        time.sleep(0.01)
threading.Thread(target=noise, daemon=True).start()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method", "")
    mid = msg.get("id")
    if mid is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "fake"}, "capabilities": {}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "big", "description": "devuelve payload grande",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "slow", "description": "tarda en responder",
             "inputSchema": {"type": "object", "properties": {}}},
        ]}})
    elif method == "tools/call":
        name = msg.get("params", {}).get("name")
        if name == "big":
            big = "y" * 200_000
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": big}], "isError": False}})
        else:
            time.sleep(2.0)
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": "lento"}], "isError": False}})
"""


def _client(name="fake") -> MCPClient:
    cfg = MCPServerConfig(
        name=name,
        command=sys.executable,
        args=["-c", FAKE_MCP_SERVER],
        tools_prefix=name,
    )
    return MCPClient(cfg)


@pytest.mark.asyncio
async def test_stderr_flood_does_not_deadlock_connect():
    """stderr verboso NO debe bloquear el handshake initialize."""
    client = _client()
    t0 = time.monotonic()
    ok = await asyncio.wait_for(client.connect(), timeout=10)
    elapsed = time.monotonic() - t0
    assert ok is True, "connect falló con stderr flooding"
    assert client._initialized is True
    assert elapsed < 8, f"connect tardó {elapsed:.1f}s (deadlock de stderr)"
    await client.disconnect()


@pytest.mark.asyncio
async def test_large_response_line_processed():
    """Respuesta >64KB en una línea no debe matar el read loop ni colgar futures."""
    client = _client()
    assert await client.connect() is True
    result = await client.call_tool("big", {})
    assert result.get("success") is True, f"respuesta grande no procesada: {str(result)[:200]}"
    text = str(result.get("output", ""))
    assert len(text) > 100_000, f"output truncado: {len(text)}"
    await client.disconnect()


@pytest.mark.asyncio
async def test_request_timeout_configurable():
    """El timeout de request usa settings.mcp_request_timeout (no 30s fijo)."""
    from core.config import settings as app_settings

    client = _client()
    assert await client.connect() is True
    with patch.object(app_settings, "mcp_request_timeout", 0.4):
        t0 = time.monotonic()
        result = await client.call_tool("slow", {})
        elapsed = time.monotonic() - t0
    assert result.get("success") is False
    assert result.get("error") == "mcp_tool_error"
    assert elapsed < 3, f"timeout configurable no aplicado ({elapsed:.1f}s)"
    await client.disconnect()


@pytest.mark.asyncio
async def test_native_alias_never_overrides_builtin():
    """Colisión robusta al orden: el alias nativo se rechaza si es un built-in."""
    mock_registry = MagicMock()
    mock_registry.list_tools.return_value = []  # vacío: solo el guard de specs salva

    client = _client()
    client._tools = {
        # native_name colisiona con un built-in real de TOOL_DEFINITIONS
        "file_manager": {"parameters": {}, "required": [], "full_name": "mcp:x.file_manager"},
        "echo": {"parameters": {}, "required": [], "full_name": "mcp:x.echo"},
    }

    with (
        patch("core.mcp.client.tools_registry", mock_registry),
        patch(
            "core.mcp.client.TOOL_DEFINITIONS",
            {"file_manager": MagicMock(), "code_exec": MagicMock()},
        ),
    ):
        await client._register_discovered_tools()

    registered_names = [c.args[0] for c in mock_registry.register.call_args_list]
    assert (
        "file_manager" not in registered_names
    ), f"alias nativo pisó el built-in: {registered_names}"
    assert "mcp_x_file_manager" in registered_names


def test_server_allowlist_filters_tools():
    """MCPServer(tools_allowlist=[...]) filtra tools/list y CLI --allow existe."""
    from core.mcp.server import MCPServer

    with patch("tools.specs.TOOL_DEFINITIONS", {}) as defs:
        defs["code_exec"] = MagicMock(description="a", parameters={}, required=[])
        defs["web_search"] = MagicMock(description="b", parameters={}, required=[])

        server_all = MCPServer()
        names_all = [t["name"] for t in server_all._get_tools()]
        assert set(names_all) == {"code_exec", "web_search"}

        server_filtered = MCPServer(tools_allowlist=["code_exec"])
        names_f = [t["name"] for t in server_filtered._get_tools()]
        assert names_f == ["code_exec"]


@pytest.mark.asyncio
async def test_server_rejects_tool_outside_allowlist():
    """La allowlist se aplica TAMBIÉN en tools/call, no solo en discovery."""
    from core.mcp.server import MCPServer

    srv = MCPServer(tools_allowlist=["file_manager"])
    calls: list[tuple] = []

    async def fake_safe_tool_call(name, arguments):
        calls.append((name, arguments))
        return {"success": True, "output": "ejecutado"}

    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "bash_manager", "arguments": {"command": "ls"}},
    }
    with patch("tools.wrapper.safe_tool_call", side_effect=fake_safe_tool_call):
        resp = await srv._handle_tools_call(req)

    err = resp.get("error") or {}
    assert err.get("code") == -32602, f"H2 REGRESIÓN: sin gate en tools/call: {resp}"
    assert "allowlist" in (err.get("message") or "").lower()
    assert resp.get("id") == 7
    assert not calls, f"la tool se ejecutó pese a estar fuera de la allowlist: {calls}"


@pytest.mark.asyncio
async def test_server_allows_listed_tool():
    """Una tool EN la allowlist pasa el gate (sin error -32602 por allowlist)."""
    from core.mcp.server import MCPServer

    srv = MCPServer(tools_allowlist=["file_manager"])

    async def fake_safe_tool_call(name, arguments):
        return {"success": True, "output": "ok"}

    req = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "file_manager", "arguments": {"action": "list"}},
    }
    with patch("tools.wrapper.safe_tool_call", side_effect=fake_safe_tool_call):
        resp = await srv._handle_tools_call(req)

    err = resp.get("error") or {}
    assert err.get("code") != -32602, f"allowlist bloqueó tool listada: {resp}"
    assert resp.get("isError") is False
    assert resp["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_server_survives_large_client_request():
    """El server Morphix REAL (MCPServer.run con StreamReader 64KB default)
    NO debe morir cuando un cliente le envía una línea >64KB."""
    import json as _json
    from pathlib import Path as _Path

    repo_root = str(_Path(__file__).resolve().parent.parent)
    big_args = "x" * 200_000

    # Hijo = server Morphix REAL con tools globales cargadas y allowlist mínima.
    CHILD = (
        "import sys\n"
        f"sys.path.insert(0, {repo_root!r})\n"
        "import logging\n"
        "logging.basicConfig(stream=sys.stderr, level=logging.ERROR)\n"
        "from tools.loader import load_global_tools\n"
        "load_global_tools()\n"
        "from core.mcp.server import MCPServer\n"
        "import asyncio\n"
        "asyncio.run(MCPServer(tools_allowlist=['code_search']).run())\n"
    )

    cfg = MCPServerConfig(
        name="realmx", command=sys.executable, args=["-c", CHILD], tools_prefix="rm"
    )
    client = MCPClient(cfg)
    # Bajo cobertura el hijo tarda >15s en arrancar (instrumentación
    # subprocess) → el handshake con mcp_init_timeout default (15s) falla y
    # connect() retorna False de forma determinista. Ampliado solo aquí.
    from core.config import settings as _settings

    with patch.object(_settings, "mcp_init_timeout", 60):
        assert await asyncio.wait_for(client.connect(), timeout=90) is True

    # Enviar directamente por el stream para controlar el tamaño exacto:
    # tools/call con argumento de 200KB (>64KB default asyncio StreamReader).
    proc_stdin = client.process.stdin
    req = {
        "jsonrpc": "2.0",
        "id": 999,
        "method": "tools/call",
        "params": {"name": "t", "arguments": {"blob": big_args}},
    }
    proc_stdin.write(_json.dumps(req).encode() + b"\n")
    await proc_stdin.drain()

    # El server SIGUE VIVO: responde un request ordinario (tool real, allowlist).
    with patch.object(_settings, "mcp_request_timeout", 20):
        result = await client.call_tool(
            "code_search",
            {"pattern": "zzz_patron_inexistente_xyz", "include": "*.py", "max_results": 1},
        )
    assert result.get("success") is True, f"server murió tras línea grande: {result}"
    await client.disconnect()


@pytest.mark.asyncio
async def test_call_tool_filters_arguments_against_declared_schema():
    """call_tool filtra arguments a las keys del inputSchema declarado."""
    from core.mcp.adapter import mcp_result_to_morphix

    client = _client()
    client._tools = {
        "echo": {
            "parameters": {"text": {"type": "string"}},
            "required": ["text"],
            "full_name": "mcp:fake.echo",
        }
    }
    client._initialized = True

    sent: dict = {}

    async def fake_send(method, params, timeout=None):
        sent.update(params)
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    with patch.object(client, "_send_request", side_effect=fake_send):
        result = await client.call_tool(
            "echo", {"text": "hi", "project_root": "/p", "workspace": "w"}
        )

    assert mcp_result_to_morphix or result  # resultado válido
    assert set(sent["arguments"].keys()) == {"text"}, f"fugas: {sorted(sent['arguments'])}"


@pytest.mark.asyncio
async def test_proxy_returns_flat_output_without_raw():
    """La proxy (ruta orquestador) debe retornar {'success','output':str},
    nunca el dict con 'raw' que acaba stringificado en el transcript."""
    from tools.registry import ToolsRegistry

    reg = ToolsRegistry()

    async def fake_call_tool(name, arguments):
        return {
            "success": True,
            "output": "texto limpio",
            "raw": {"content": [{"type": "text", "text": "texto limpio"}]},
        }

    client = _client()
    client.call_tool = fake_call_tool  # type: ignore[method-assign]
    client._tools = {"big": {"parameters": {}, "required": [], "full_name": "mcp:fake.big"}}
    with patch("core.mcp.client.tools_registry", reg):
        await client._register_discovered_tools()

    fn = reg.get_tool("mcp_fake_big")
    assert fn is not None
    result = await fn(blob="x")
    assert set(result.keys()) == {"success", "output"}, f"keys: {sorted(result)}"
    assert isinstance(result["output"], str)
    assert result["output"] == "texto limpio"


@pytest.mark.asyncio
async def test_disconnect_unregisters_own_tools():
    """disconnect() debe desregistrar TOOL_DEFINITIONS + registry entries
    propias (identity-check: no matar alias re-registrados por otro cliente)."""
    from tools.registry import ToolsRegistry
    from tools.specs import TOOL_DEFINITIONS as DEFS

    reg = ToolsRegistry()
    client = _client()
    client._tools = {
        "alpha": {"parameters": {}, "required": [], "full_name": "mcp:f.alpha"},
        "beta": {"parameters": {}, "required": [], "full_name": "mcp:f.beta"},
    }
    with (
        patch("core.mcp.client.tools_registry", reg),
        patch("core.mcp.client.TOOL_DEFINITIONS", DEFS),
    ):
        await client._register_discovered_tools()
        assert "mcp_f_alpha" in DEFS
        assert reg.get_tool("mcp_f_alpha") is not None

        await client.disconnect()

    assert "mcp_f_alpha" not in DEFS, "TOOL_DEFINITIONS leak"
    assert reg.get_tool("mcp_f_alpha") is None, "registry leak"
    assert reg.get_tool("mcp_f_beta") is None


@pytest.mark.asyncio
async def test_malformed_tool_entries_skipped_not_fatal():
    """Una entrada malformada en tools/list NO derriba la conexión."""
    SERVER_MALFORMED = (
        "import sys, json\n"
        "def send(o):\n"
        "    sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line: continue\n"
        "    msg = json.loads(line); mid = msg.get('id')\n"
        "    if mid is None: continue\n"
        "    if msg.get('method') == 'initialize':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{}})\n"
        "    elif msg.get('method') == 'tools/list':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'tools':["
        "{'name':'good','inputSchema':{'type':'object','properties':{}}},"
        "{'sin_nombre':True},{\"name\":123}]}})\n"
    )
    from tools.registry import ToolsRegistry

    reg = ToolsRegistry()
    cfg = MCPServerConfig(
        name="mf", command=sys.executable, args=["-c", SERVER_MALFORMED], tools_prefix="mf"
    )
    client = MCPClient(cfg)
    with patch("core.mcp.client.tools_registry", reg):
        ok = await asyncio.wait_for(client.connect(), timeout=15)
    assert ok is True, "una entrada malformada no debe derribar la conexión"
    assert "good" in client.tools and len(client.tools) == 1
    await client.disconnect()


@pytest.mark.asyncio
async def test_tools_list_follows_cursor_pagination():
    """tools/list sigue nextCursor hasta agotar páginas (cota 50)."""
    SERVER_PAGED = (
        "import sys, json\n"
        "def send(o):\n"
        "    sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line: continue\n"
        "    msg = json.loads(line); mid = msg.get('id'); method = msg.get('method')\n"
        "    if mid is None: continue\n"
        "    if method == 'initialize':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{}})\n"
        "    elif method == 'tools/list':\n"
        "        cur = msg.get('params',{}).get('cursor')\n"
        "        if cur == 'page2':\n"
        "            send({'jsonrpc':'2.0','id':mid,'result':{'tools':["
        "{'name':'second','inputSchema':{'type':'object','properties':{}}}]}})\n"
        "        else:\n"
        "            send({'jsonrpc':'2.0','id':mid,'result':{'tools':["
        "{'name':'first','inputSchema':{'type':'object','properties':{}}}],"
        "'nextCursor':'page2'}})\n"
    )
    from tools.registry import ToolsRegistry

    reg = ToolsRegistry()
    cfg = MCPServerConfig(
        name="pg", command=sys.executable, args=["-c", SERVER_PAGED], tools_prefix="pg"
    )
    client = MCPClient(cfg)
    with patch("core.mcp.client.tools_registry", reg):
        assert await asyncio.wait_for(client.connect(), timeout=15) is True
    assert set(client.tools) == {"first", "second"}
    await client.disconnect()


@pytest.mark.asyncio
async def test_init_timeout_bounded_by_mcp_init_timeout(monkeypatch):
    """Server que no responde initialize → connect False acotado por
    settings.mcp_init_timeout (no el timeout general de 120s)."""
    from core.config import settings as s

    SERVER_HANG_INIT = "import sys, time\n" "time.sleep(30)\n"
    monkeypatch.setattr(s, "mcp_init_timeout", 0.5)
    cfg = MCPServerConfig(
        name="hang", command=sys.executable, args=["-c", SERVER_HANG_INIT], tools_prefix="hg"
    )
    client = MCPClient(cfg)

    import time as _t

    t0 = _t.monotonic()
    ok = await asyncio.wait_for(client.connect(), timeout=10)
    elapsed = _t.monotonic() - t0
    assert ok is False
    assert elapsed < 5, f"init timeout no acotado: {elapsed:.1f}s"
    await client.disconnect()


def test_connect_mcp_servers_runs_in_parallel(monkeypatch):
    """La conexión de múltiples servers es concurrente (gather)."""
    import time

    import core.mcp.client as mcp_client_mod

    delays = {"a": 0.4, "b": 0.4}

    class FakeClient:
        def __init__(self, config):
            self.config = config

        async def connect(self):
            await asyncio.sleep(delays[self.config.name])
            return True

    created: list[FakeClient] = []

    class FakeCfg:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(
        "core.mcp.config.load_mcp_servers",
        lambda ws: [FakeCfg("a"), FakeCfg("b")],
    )
    monkeypatch.setattr(mcp_client_mod, "MCPClient", FakeClient)
    orig_clients = dict(mcp_client_mod._clients)
    mcp_client_mod._clients.clear()
    connected: set = set()
    try:
        t0 = time.monotonic()
        asyncio.run(mcp_client_mod.connect_mcp_servers("ws"))
        elapsed = time.monotonic() - t0
        connected = set(mcp_client_mod._clients.keys())
    finally:
        mcp_client_mod._clients.clear()
        mcp_client_mod._clients.update(orig_clients)
    assert connected == {"a", "b"}
    # Secuencial sería ~0.8s; paralelo ~0.4s. Margen holgado para CI lento.
    assert elapsed < 0.75, f"conexión no paralela: {elapsed:.2f}s"


def _bare_client(prefix: str, server_name: str) -> MCPClient:
    """Cliente sin subprocess: solo registro de specs/proxies."""
    cfg = MCPServerConfig(name=server_name, command="noop", args=[], tools_prefix=prefix)
    c = MCPClient(cfg)
    c._tools = {"echo": {"parameters": {}, "required": [], "full_name": f"mcp:{prefix}.echo"}}
    return c


@pytest.mark.asyncio
async def test_second_server_same_tool_is_skipped():
    """Dos servers con mismo prefix+native ⇒ el segundo NO pisa la spec
    ni el proxy del primero (warning + skip)."""
    from core.mcp.client import _SPEC_OWNERS
    from tools.specs import TOOL_DEFINITIONS

    a = _bare_client("x", "serverA")
    b = _bare_client("x", "serverB")

    with patch("core.mcp.client.tools_registry") as reg:
        await a._register_discovered_tools()
        n_after_a = len(reg.register.call_args_list)
        assert TOOL_DEFINITIONS["mcp_x_echo"].description.startswith("[MCP:serverA]")

        await b._register_discovered_tools()

        assert (
            len(reg.register.call_args_list) == n_after_a
        ), "N-4 REGRESIÓN: serverB registró encima de serverA"
    assert TOOL_DEFINITIONS["mcp_x_echo"].description.startswith("[MCP:serverA]")
    assert _SPEC_OWNERS["mcp_x_echo"] is a


@pytest.mark.asyncio
async def test_disconnect_only_pops_owned_spec():
    """disconnect borra la spec SOLO si este cliente es el dueño."""
    from core.mcp.client import _SPEC_OWNERS
    from tools.specs import TOOL_DEFINITIONS

    a = _bare_client("x", "serverA")
    b = _bare_client("x", "serverB")

    with patch("core.mcp.client.tools_registry"):
        await a._register_discovered_tools()
        await b._register_discovered_tools()  # skipped por colisión

    await b.disconnect()
    assert "mcp_x_echo" in TOOL_DEFINITIONS, "B borró la spec de A"
    assert "mcp_x_echo" not in _SPEC_OWNERS or _SPEC_OWNERS["mcp_x_echo"] is a

    await a.disconnect()
    assert "mcp_x_echo" not in TOOL_DEFINITIONS, "A debe limpiar su propia spec"
    assert "mcp_x_echo" not in _SPEC_OWNERS


@pytest.mark.asyncio
async def test_overflow_receives_jsonrpc_error_before_close():
    """Ante línea >límite el server responde -32700 (id null) ANTES de
    cerrar — no muere silencioso con EOF."""
    import json as _json
    from pathlib import Path as _Path

    repo_root = str(_Path(__file__).resolve().parent.parent)
    CHILD = (
        "import sys\n"
        f"sys.path.insert(0, {repo_root!r})\n"
        "import logging\n"
        "logging.basicConfig(stream=sys.stderr, level=logging.ERROR)\n"
        "from core.mcp.server import MCPServer\n"
        "import asyncio\n"
        "asyncio.run(MCPServer(tools_allowlist=[]).run())\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        # Handshake manual mínimo (sin MCPClient: su _read_loop roba stdout).
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        proc.stdin.write(_json.dumps(req).encode() + b"\n")
        await proc.stdin.drain()
        init_line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
        assert init_line.strip(), "server no respondió initialize"

        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        proc.stdin.write(_json.dumps(note).encode() + b"\n")
        await proc.stdin.drain()

        # Línea gigante (>4MB default MCP_MAX_LINE_BYTES) con id válido previo.
        big = _json.dumps({"jsonrpc": "2.0", "id": 5, "method": "x"}).encode() + b"y" * 200_000
        proc.stdin.write(big + b"\n")
        await proc.stdin.drain()

        err_line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
        assert err_line.strip(), "T4 REGRESIÓN: server cerró sin responder -32700"
        resp = _json.loads(err_line.decode())
        assert resp.get("error", {}).get("code") == -32700, f"inesperado: {resp}"
        assert resp.get("id") is None

        # Tras responder, cierra limpio (EOF).
        tail = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        assert tail == b""
    finally:
        with contextlib.suppress(Exception):
            proc.kill()


@pytest.mark.asyncio
async def test_call_tool_warns_when_no_schema_declared(caplog):
    """Sin inputSchema declarado los args viajan sin filtrar — es
    deliberado pero debe quedar auditado en logs."""
    from core.mcp.adapter import mcp_result_to_morphix  # noqa: F401

    client = _client()
    client._tools = {"loose": {"parameters": {}, "required": [], "full_name": "mcp:f.loose"}}
    client._initialized = True

    async def fake_send(method, params, timeout=None):
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    with (
        patch.object(client, "_send_request", side_effect=fake_send),
        caplog.at_level("WARNING", logger="core.mcp.client"),
    ):
        await client.call_tool("loose", {"a": 1, "b": 2})

    assert any(
        "sin inputSchema" in r.message and "'2'" in r.message for r in caplog.records
    ), f"sin warning de fallback: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_reconnect_backoff_exponential_gives_up_after_max(monkeypatch):
    """Delays exponenciales (1,2,4...) y rendición tras MAX intentos."""
    from core.mcp import client as mcp_mod

    client = MCPClient(MCPServerConfig(name="bo", command="noop", args=[], tools_prefix="b"))
    sleeps: list[float] = []
    connect_calls = {"n": 0}

    async def fake_sleep(s):
        sleeps.append(round(s))

    async def always_fail():
        connect_calls["n"] += 1
        return False

    fut = asyncio.get_running_loop().create_future()
    real_sleep = asyncio.sleep

    async def noop_disconnect():
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(client, "connect", always_fail)
    monkeypatch.setattr(client, "disconnect", noop_disconnect)
    monkeypatch.setattr(client, "_pending", {"x": fut})
    monkeypatch.setattr(mcp_mod.random, "uniform", lambda a, b: 1.0)

    client._reconnecting = False
    client._reconnect_attempts = 0
    client._schedule_reconnect()
    # esperar a que la tarea termine (poll)
    for _ in range(500):
        if not client._reconnecting:
            break
        await real_sleep(0)  # yield REAL al event loop (el sleep está parcheado)
    assert not client._reconnecting
    assert client._reconnect_attempts == mcp_mod._MAX_RECONNECT_ATTEMPTS
    assert len(sleeps) == mcp_mod._MAX_RECONNECT_ATTEMPTS
    assert sleeps == sorted(sleeps), sleeps  # creciente
    assert sleeps[0] >= 0 and all(s <= 30 for s in sleeps)
    assert fut.done() and isinstance(fut.exception(), ConnectionError)


ENV_PROBE_MCP_SERVER = r"""
import sys, json, os

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    mid = msg.get("id")
    method = msg.get("method", "")
    if mid is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "envprobe"}, "capabilities": {}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "probe", "description": "reporta env del server",
             "inputSchema": {"type": "object", "properties": {}}}]}})
    elif method == "tools/call":
        secret = os.environ.get("DEEPSEEK_API_KEY")
        path = os.environ.get("PATH", "")
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [
            {"type": "text",
             "text": f"secret={'SET' if secret else 'CLEAN'} path={'OK' if path else 'MISSING'}"}],
            "isError": False}})
"""


@pytest.mark.asyncio
async def test_mcp_spawn_env_allowlisted(monkeypatch):
    """El subprocess MCP nace con env allowlist — los secretos del
    proceso padre NO se heredan; PATH sigue resolviendo binarios."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret123")
    cfg = MCPServerConfig(
        name="envprobe",
        command=sys.executable,
        args=["-c", ENV_PROBE_MCP_SERVER],
        tools_prefix="ep",
    )
    client = MCPClient(cfg)
    assert await asyncio.wait_for(client.connect(), timeout=15) is True
    res = await client.call_tool("probe", {})
    await client.disconnect()
    text = str(res.get("output", ""))
    assert res.get("success") is True, f"probe falló: {text[:200]}"
    assert "secret=CLEAN" in text, f"C1c REGRESIÓN: server vio el secreto ({text})"
    assert "path=OK" in text, f"PATH ausente en el server ({text})"
