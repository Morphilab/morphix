# core/mcp/client.py
"""MCP client — connect to external MCP servers, discover and proxy their tools."""

import asyncio
import contextlib
import logging
import random
import re

from core.config import settings
from core.mcp.adapter import mcp_result_to_morphix, mcp_tool_to_morphix_params
from core.mcp.config import MCPServerConfig
from core.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    build_notification,
    build_request,
    get_id,
    is_response,
    read_message,
    write_message,
)
from core.utils import build_child_env
from tools.registry import tools_registry
from tools.specs import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

# Global registry of connected MCP clients
_clients: dict[str, "MCPClient"] = {}

# Propietario de cada spec registrada (sanitized_name -> cliente).
# Evita que un segundo server MCP pise silenciosamente las specs/proxies
# del primero, y que un disconnect ajeno borre especificaciones ajenas.
_SPEC_OWNERS: dict[str, "MCPClient"] = {}

# MCP: reconexión con backoff exponencial — un solo reintento fijo deja
# clientes zombis ante servers caídos de forma prolongada.
_MAX_RECONNECT_ATTEMPTS = 5
_RECONNECT_BASE_DELAY = 1.0
_RECONNECT_MAX_DELAY = 30.0

# Los nombres y descripciones de tools remotas aterrizan en
# TOOL_DEFINITIONS → payload de function-calling de CADA llamada LLM.
# Un server remoto hostil/bugueado puede devolver un nombre con caracteres
# de control o de 10MB de longitud (rompe todas las llamadas posteriores)
# o una descripción gigante (engorda cada prompt). Charset seguro + topes.
_MCP_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")
_MCP_MAX_NAME_LEN = 64
_MCP_MAX_DESC_CHARS = 2000


def _sanitize_mcp_tool_name(full_name: str) -> str:
    """Nombre apto para function-calling: `:`/`.` → `_`, charset seguro,
    longitud acotada. Nombres legítimos no cambian."""
    name = full_name.replace(":", "_").replace(".", "_")
    return _MCP_UNSAFE_NAME_CHARS.sub("_", name)[:_MCP_MAX_NAME_LEN]


class MCPClient:
    """Manages one MCP server connection (subprocess via stdio)."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int | str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnecting = False
        self._reconnect_attempts = 0
        self._tools: dict[str, dict] = {}  # tool_name -> schema
        self._initialized = False
        # registro de lo que ESTE cliente registró (spec + callables),
        # para desregistrar en disconnect sin matar registros ajenos.
        self._registered: dict[str, object] = {}

    async def connect(self, register_tools: bool = True) -> bool:
        """Spawn the MCP server subprocess and perform initialization handshake."""
        if self.process is not None:
            return True

        # Los servers de terceros no ven secretos; escape-hatch: campo
        # env del server en mcp.json (MCPServerConfig.env, aplicado justo abajo)
        env = build_child_env()
        env.update(self.config.env)

        try:
            self.process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=max(64 * 1024, int(settings.mcp_max_line_bytes)),
            )
        except FileNotFoundError:
            logger.error(
                f"MCP server '{self.config.name}': command not found: {self.config.command}"
            )
            return False
        except Exception:
            logger.exception(f"MCP server '{self.config.name}': failed to start")
            return False

        # Drenar stderr en tarea dedicada — sin esto, un servidor
        # verboso llena el pipe (~64KB) y se bloquea ESCRIBIENDO stderr,
        # dejando de responder por stdout (deadlock).
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        # Start background reader
        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize handshake (timeout propio acotado — un server que
        # no responde initialize no debe bloquear 120s).
        init_timeout = float(getattr(settings, "mcp_init_timeout", 15.0))
        try:
            result = await self._send_request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "morphix", "version": "1.0"},
                },
                timeout=init_timeout,
            )
            logger.info(
                f"MCP server '{self.config.name}' initialized: "
                f"{result.get('serverInfo', {}).get('name', 'unknown')} "
                f"v{result.get('protocolVersion', '?')}"
            )
        except Exception as e:
            logger.error(f"MCP server '{self.config.name}' init failed: {e}")
            await self.disconnect()
            return False

        # Send initialized notification
        try:
            stdin = self.process.stdin
            if stdin is None:
                logger.error(f"MCP server '{self.config.name}' has no stdin stream")
                return False
            await write_message(
                stdin,
                build_notification("notifications/initialized"),
            )
        except Exception:
            logger.warning(f"MCP server '{self.config.name}' initialized notification failed")

        # Discover tools
        try:
            # seguir nextCursor hasta agotar páginas (cota dura anti-loop).
            cursor: str | None = None
            pages = 0
            discovered = 0
            while True:
                params: dict = {}
                if cursor:
                    params["cursor"] = cursor
                result = await self._send_request("tools/list", params, timeout=init_timeout)
                tools = result.get("tools", [])
                for tool in tools if isinstance(tools, list) else []:
                    # MCP: entradas malformadas se saltan con warning,
                    # NO derriban la conexión completa.
                    if not isinstance(tool, dict):
                        continue
                    native = tool.get("name")
                    if not native or not isinstance(native, str):
                        logger.warning(
                            f"MCP server '{self.config.name}': skipping malformed tool "
                            f"entry (missing/invalid name): {str(tool)[:100]}"
                        )
                        continue
                    full_name = f"mcp:{self.config.tools_prefix}.{native}"
                    self._tools[native] = mcp_tool_to_morphix_params(tool)
                    self._tools[native]["full_name"] = full_name
                    discovered += 1
                cursor = result.get("nextCursor")
                pages += 1
                if not cursor or pages >= 50:
                    break
            logger.info(f"MCP server '{self.config.name}': {discovered} tool(s) discovered")
        except Exception as e:
            logger.error(f"MCP server '{self.config.name}' tools/list failed: {e}")
            await self.disconnect()
            return False

        # Register tools in Morphix tool registry
        if register_tools:
            await self._register_discovered_tools()

        self._initialized = True
        return True

    async def _register_discovered_tools(self) -> None:
        """Register discovered MCP tools in Morphix tools_registry."""
        from tools.specs import ToolDefinition

        for native_name, params in self._tools.items():
            full_name = params.get("full_name", f"mcp:{self.config.tools_prefix}.{native_name}")
            sanitized_name = _sanitize_mcp_tool_name(full_name)
            if not sanitized_name.strip("_"):
                logger.warning(
                    f"MCP server '{self.config.name}': tool '{native_name}' saneada a "
                    f"nombre vacío — omitida"
                )
                continue
            description = f"[MCP:{self.config.name}] {params.get('description', '')}"
            if len(description) > _MCP_MAX_DESC_CHARS:
                description = description[:_MCP_MAX_DESC_CHARS] + "…"

            # Create a closure that calls this client for the specific tool
            client_ref = self
            tool_native_name = native_name

            async def _mcp_proxy(
                _native=tool_native_name,
                _client=client_ref,
                **kwargs,
            ):
                result = await _client.call_tool(_native, kwargs)
                # aplanar — la ruta orquestador envuelve este dict como
                # output y el loop lo serializa; 'raw' duplicaría el payload
                # en el transcript del LLM.
                return {
                    "success": bool(result.get("success")),
                    "output": str(result.get("output", "")),
                }

            _mcp_proxy.__name__ = sanitized_name

            # Register in tool specs with SANITIZED name for OpenAI function-calling
            # si otro cliente ya expone este nombre saneado, no pisar.
            owner = _SPEC_OWNERS.get(sanitized_name)
            if owner is not None and owner is not self:
                logger.warning(
                    f"MCP: '{sanitized_name}' ya expuesto por server "
                    f"'{owner.config.name}'; tool '{native_name}' de "
                    f"'{self.config.name}' omitida"
                )
                continue

            TOOL_DEFINITIONS[sanitized_name] = ToolDefinition(
                name=sanitized_name,
                description=description,
                parameters=params.get("parameters", {}),
                required=params.get("required", []),
            )
            _SPEC_OWNERS[sanitized_name] = self
            self._registered[f"spec:{sanitized_name}"] = True

            existing = tools_registry.get_tool(sanitized_name)
            tools_registry.register(sanitized_name)(_mcp_proxy)
            self._registered[f"reg:{sanitized_name}"] = (_mcp_proxy, existing)
            # Also register with original full name as alias
            if full_name != sanitized_name and full_name not in tools_registry.list_tools():
                tools_registry.register(full_name)(_mcp_proxy)
                self._registered[f"reg:{full_name}"] = (_mcp_proxy, None)
            # Also register with native short name as alias
            # robusto al orden — si el nombre nativo es un BUILT-IN
            # (TOOL_DEFINITIONS), NUNCA registrar el alias corto aunque el
            # MCP haya cargado antes que las tools globales.
            if (
                native_name not in tools_registry.list_tools()
                and native_name not in TOOL_DEFINITIONS
            ):
                tools_registry.register(native_name)(_mcp_proxy)
                self._registered[f"reg:{native_name}"] = (_mcp_proxy, None)
            logger.debug(f"MCP tool registered: {sanitized_name}")

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server. Returns Morphix-format result dict."""
        if not self._initialized:
            return {
                "success": False,
                "error": "mcp_not_connected",
                "output": f"MCP server '{self.config.name}' not connected",
            }

        # Strip mcp: prefix to get the native tool name
        native_name = name.split(".", 1)[-1] if "." in name else name

        # no enviar kwargs internos del host (project_root/workspace) al
        # server remoto — filtrar a las keys declaradas en su inputSchema.
        declared = self._tools.get(native_name, {}).get("parameters")
        if isinstance(declared, dict) and declared:
            allowed = set(declared.keys())
            remote_args = {k: v for k, v in arguments.items() if k in allowed}
        else:
            # fallback deliberado (servers sin schema) — auditable.
            logger.warning(
                f"MCP tool '{name}' sin inputSchema declarado: viajan "
                f"'{len(arguments)}' argumentos sin filtrar al server remoto"
            )
            remote_args = dict(arguments)

        try:
            result = await self._send_request(
                "tools/call",
                {"name": native_name, "arguments": remote_args},
            )
            return mcp_result_to_morphix(result)
        except Exception as e:
            return {
                "success": False,
                "error": "mcp_tool_error",
                "output": f"MCP tool '{name}' error: {e}",
            }

    async def _send_request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        if timeout is None:
            timeout = float(settings.mcp_request_timeout)
        if self.process is None or self.process.stdin is None:
            raise ConnectionError("MCP client not connected")

        self._request_id += 1
        msg_id = self._request_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        # el write DENTRO del try: si el pipe del proceso muerto revienta,
        # el finally saca el future de _pending — una entrada huérfana aquí
        # envenena el disconnect con "Future exception was never retrieved".
        try:
            await write_message(self.process.stdin, build_request(msg_id, method, params))
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _read_loop(self) -> None:
        """Background task: read responses from server stdout and resolve futures."""
        if self.process is None or self.process.stdout is None:
            return
        try:
            while True:
                msg = await read_message(self.process.stdout)
                if is_response(msg):
                    msg_id = get_id(msg)
                    if msg_id is not None and msg_id in self._pending:
                        if "error" in msg:
                            self._pending[msg_id].set_exception(
                                RuntimeError(msg["error"].get("message", "MCP error"))
                            )
                        else:
                            self._pending[msg_id].set_result(msg.get("result", {}))
        except ValueError as e:
            # línea > límite del StreamReader — NO matar el loop en silencio:
            # rechazar pendientes honestamente y programar reconexión.
            logger.error(f"MCP server '{self.config.name}' line overflow: {e}")
            self._reject_pending_and_reconnect("mcp_line_limit_exceeded")
        except ConnectionError:
            # proceso muerto / EOF: mismo tratamiento que line-overflow —
            # sin esto el cliente queda zombi (spec registrada, llamadas
            # condenadas al timeout completo, sin reconexión jamás).
            logger.info(f"MCP server '{self.config.name}' stream closed")
            self._reject_pending_and_reconnect("mcp_stream_closed")
        except asyncio.CancelledError:
            logger.warning("MCP read loop cancelled", exc_info=True)
        except Exception:
            # el read loop murió por algo inesperado: también es un zombi
            # en potencia — fallar pendientes y reintentar en background.
            logger.exception(f"MCP server '{self.config.name}' read loop error")
            self._reject_pending_and_reconnect("mcp_read_loop_error")

    def _reject_pending_and_reconnect(self, reason: str) -> None:
        """Rechazar TODOS los pendientes con `reason` y programar reconexión.

        Dueño único del camino de recuperación del read loop: sin él, la
        muerte del server deja la spec registrada y cada llamada espera
        su timeout completo antes de fallar.
        """
        for _msg_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending.clear()
        self._schedule_reconnect()

    async def _drain_stderr(self) -> None:
        """Consume stderr del subprocess continuamente (anti-deadlock)."""
        if self.process is None or self.process.stderr is None:
            return
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                logger.debug(
                    "MCP[%s] stderr: %s",
                    self.config.name,
                    line.decode(errors="replace").strip()[:200],
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("MCP stderr drain ended", exc_info=True)

    def _schedule_reconnect(self) -> None:
        """Reconexión en background con backoff exponencial."""
        if self._reconnecting:
            return
        self._reconnecting = True

        async def _reconnect() -> None:
            try:
                while self._reconnect_attempts < _MAX_RECONNECT_ATTEMPTS:
                    delay = min(
                        _RECONNECT_BASE_DELAY * (2**self._reconnect_attempts),
                        _RECONNECT_MAX_DELAY,
                    )
                    delay *= random.uniform(0.85, 1.15)
                    logger.info(
                        f"MCP server '{self.config.name}': reconnecting "
                        f"(intento {self._reconnect_attempts + 1}/{_MAX_RECONNECT_ATTEMPTS}) "
                        f"en {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    try:
                        await self.disconnect()
                        ok = await self.connect()
                    except Exception:
                        logger.exception(f"MCP server '{self.config.name}' reconnect failed")
                        ok = False
                    if ok:
                        self._reconnect_attempts = 0
                        return
                    self._reconnect_attempts += 1
                # Agotado: falla ruidosa a los pendientes y suelta la bandera.
                logger.error(
                    f"MCP server '{self.config.name}': reconexión agotada tras "
                    f"{_MAX_RECONNECT_ATTEMPTS} intentos"
                )
                for _msg_id, future in list(self._pending.items()):
                    if not future.done():
                        future.set_exception(
                            ConnectionError(f"MCP server '{self.config.name}': reconnect exhausted")
                        )
                self._pending.clear()
            finally:
                self._reconnecting = False
                self._reconnect_task = None

        # asyncio solo guarda referencias DÉBILES a tareas: una tarea
        # fire-and-forget sin dueño puede ser recolectada a mitad de
        # reconexión — retenerla hasta que termine (o disconnect la cancele).
        self._reconnect_task = asyncio.get_running_loop().create_task(_reconnect())

    async def disconnect(self) -> None:
        """Terminate the MCP server subprocess."""
        # desregistrar SOLO lo que este cliente registró (identity-check:
        # si otro cliente re-registró el mismo nombre con SU proxy, no tocar).
        for key in list(self._registered):
            if key.startswith("spec:"):
                # pop solo si este cliente es el dueño de la spec.
                spec_name = key[5:]
                if _SPEC_OWNERS.get(spec_name) is self:
                    TOOL_DEFINITIONS.pop(spec_name, None)
                    _SPEC_OWNERS.pop(spec_name, None)
                continue
            if not key.startswith("reg:"):
                continue
            name = key[4:]
            pair_obj = self._registered[key]
            if not isinstance(pair_obj, tuple) or len(pair_obj) != 2:
                continue
            own_func, previous = pair_obj
            current = tools_registry.get_tool(name)
            if current is own_func:
                tools_registry.unregister(name)
                if previous is not None:
                    # restaurar el callable previo (colisión de alias)
                    tools_registry.register(name)(previous)
        self._registered.clear()

        # una reconexión en vuelo no debe sobrevivir a un disconnect manual;
        # si el disconnect VIENE de la propia reconexión, no auto-cancelarse
        # ni limpiar sus banderas (el loop sigue con ellas intactas —
        # limpiarlas aquí dejaría la puerta abierta a una segunda
        # reconexión concurrente sobre el mismo cliente).
        current_task = asyncio.current_task()
        if self._reconnect_task is not None and self._reconnect_task is not current_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
            self._reconnecting = False

        for attr in ("_reader_task", "_stderr_task"):
            task = getattr(self, attr, None)
            if task is not None:
                setattr(self, attr, None)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        # Reject all pending futures
        for _msg_id, future in self._pending.items():
            if not future.done():
                future.set_exception(ConnectionError("MCP client disconnected"))
        self._pending.clear()

        if self.process is not None:
            # returncode is None ⇔ sigue vivo: terminate/kill sobre un
            # proceso ya muerto solo produce el doble ProcessLookupError.
            if self.process.returncode is None:
                try:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                except (ProcessLookupError, TimeoutError):
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
            self.process = None
        self._initialized = False
        self._tools.clear()
        logger.info(f"MCP server '{self.config.name}' disconnected")

    @property
    def tools(self) -> dict[str, dict]:
        return self._tools


async def connect_mcp_servers(workspace: str) -> None:
    """Connect to all MCP servers configured for a workspace (en paralelo).

    Cada servidor conectado queda registrado como trusted PARA este
    workspace (momento de declaración). Otros workspaces del proceso no
    heredan nada.
    """
    from core.mcp.config import load_mcp_servers
    from core.mcp.trust import record_server_trust

    configs = [cfg for cfg in load_mcp_servers(workspace) if cfg.name not in _clients]

    async def _connect_one(cfg: MCPServerConfig) -> None:
        client = MCPClient(cfg)
        if await client.connect():
            _clients[cfg.name] = client
            record_server_trust(workspace, cfg.name)

    if configs:
        await asyncio.gather(*(_connect_one(c) for c in configs))


async def disconnect_mcp_servers() -> None:
    """Disconnect all MCP clients (y su registro de trust — ciclo parejo)."""
    from core.mcp.trust import reset_trust

    for name in list(_clients):
        await _clients[name].disconnect()
    _clients.clear()
    reset_trust()


def get_mcp_client_for_tool(tool_name: str) -> "MCPClient | None":
    """Find the MCP client that owns a given tool name (colon or sanitized)."""
    if tool_name.startswith("mcp:"):
        prefix = tool_name[4:].rsplit(".", 1)[0] if "." in tool_name else tool_name[4:]
        for client in _clients.values():
            if client.config.tools_prefix == prefix:
                return client
        return None
    if tool_name.startswith("mcp_"):
        return _SPEC_OWNERS.get(tool_name)
    return None
