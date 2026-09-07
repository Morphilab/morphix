import asyncio
import logging
import threading
from collections.abc import Callable

from core.database import (
    create_schema,
    create_tables_in_schema,
    drop_schema,
    list_schemas,
    set_async_schema,
)
from core.memory.manager import memory
from core.workflow_state import switch_workspace as switch_workflow_state

logger = logging.getLogger(__name__)

# Workspace bootstrap: siempre existe (startup_db la crea al arrancar) y es
# el destino de TODOS los fallbacks de seguridad (nombre vacío, reintentos
# agotados, fallo de switch, delete del workspace activo).
DEFAULT_WORKSPACE_NAME = "main"


# ── Contador in-memory de workflows en ejecución ──
# Deny-by-default: switch_workspace() se niega mientras haya runs activos
# (salvo force=True). El contador vive solo en este proceso — tras un restart
# vale 0, por eso resume_workflow NO toma token (es continuación, no run nuevo).
_run_lock = threading.Lock()
_active_runs = 0
# tokens de runs ACTIVOS (monotónicos) — permiten detectar
# colisiones de escritura entre workflows concurrentes (distinto de la cuenta).
_active_run_tokens: set[int] = set()
_run_token_counter = 0


def begin_workflow_run() -> int:
    """Marca un workflow en ejecución. Retorna token monotónico único."""
    global _active_runs, _run_token_counter
    with _run_lock:
        _active_runs += 1
        _run_token_counter += 1
        _active_run_tokens.add(_run_token_counter)
        return _run_token_counter


def end_workflow_run(token: int) -> None:
    """Marca el fin de un workflow (idempotente: nunca baja de 0)."""
    global _active_runs
    with _run_lock:
        _active_runs = max(0, _active_runs - 1)
        _active_run_tokens.discard(token)


def is_run_active(token: int) -> bool:
    """True si el token corresponde a un workflow en ejecución."""
    with _run_lock:
        return token in _active_run_tokens


def workflow_running() -> bool:
    """True si hay al menos un workflow en ejecución en este proceso."""
    with _run_lock:
        return _active_runs > 0


class Workspaces:
    def __init__(self):
        self.current = DEFAULT_WORKSPACE_NAME
        self._switch_lock: asyncio.Lock | None = None
        self._switch_lock_loop: asyncio.AbstractEventLoop | None = None
        # vetos multi-observador (dict token→callback). Cada observador
        # (p.ej. un pane Maestro) registra su guard; el switch se bloquea si
        # CUALQUIERA veta. `set_switch_guard` se conserva como alias de add.
        self._switch_guards: dict[int, Callable] = {}
        self._guard_counter = 0

    def _get_switch_lock(self) -> asyncio.Lock:
        """Return a lock bound to the current running loop (per-loop pattern)."""
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if self._switch_lock is None or (loop is not None and self._switch_lock_loop is not loop):
            self._switch_lock = asyncio.Lock()
            self._switch_lock_loop = loop
        return self._switch_lock

    async def list_workspaces(self) -> list[str]:
        """Lista workspaces disponibles: schemas DB ∪ directorios de disco.

        Un workspace puede existir solo en disco (clonado o provisionado desde
        templates sin schema aún): debe aparecer en el switcher — el primer
        switch crea schema+tablas+bootstrap de forma idempotente.
        """
        try:
            found = set(await list_schemas())
        except Exception:
            logger.exception("Error listing schemas")
            found = set()
        found.update(self._disk_workspace_names())
        return sorted(found)

    @staticmethod
    def _disk_workspace_names() -> list[str]:
        """Nombres de workspaces presentes como directorios válidos en disco.

        Válidos: nombre `[a-z][a-z0-9_]*` + al menos un subdirectorio marcador
        (agents/workflows/tools/hooks/skills).
        """
        import re

        from core.path_resolver import paths

        base = paths.workspaces_base()
        markers = ("agents", "workflows", "tools", "hooks", "skills")
        found: list[str] = []
        try:
            entries = list(base.iterdir())
        except OSError:
            return []
        for entry in entries:
            if not entry.is_dir() or not re.match(r"^[a-z][a-z0-9_]*$", entry.name):
                continue
            if any((entry / marker).is_dir() for marker in markers):
                found.append(entry.name)
        return found

    def add_switch_guard(self, guard: Callable[[str], bool]) -> int:
        """Registra un callback veto para cambios de workspace.

        `guard(name)` retorna False para bloquear el switch. Retorna un token
        para `remove_switch_guard`. Multi-observador: el switch se veta si
        CUALQUIERA de los guards registrados rechaza.
        """
        self._guard_counter += 1
        self._switch_guards[self._guard_counter] = guard
        return self._guard_counter

    def remove_switch_guard(self, token: int) -> None:
        """Desregistra un guard por su token (no-op si ya no existe)."""
        self._switch_guards.pop(token, None)

    def set_switch_guard(self, guard: Callable[[str], bool] | None) -> int:
        """Backward-compat: añade un guard. `None` limpia TODOS los guards."""
        if guard is None:
            self._switch_guards.clear()
            return -1
        return self.add_switch_guard(guard)

    async def switch_workspace(self, name: str, retries: int = 1, force: bool = False) -> bool:
        if not name or not name.strip():
            logger.error("Empty workspace name, using '%s'", DEFAULT_WORKSPACE_NAME)
            name = DEFAULT_WORKSPACE_NAME
        name = self._validate_workspace_name(name)

        # deny-by-default — no cambiar de workspace mientras corre un
        # workflow (el bound_schema de G1 protege la sesión en curso, pero un
        # switch a mitad sigue siendo sorpresa operativa). force=True escapa.
        if workflow_running() and not force:
            logger.warning(
                "Switch a '%s' denegado: hay un workflow en ejecución (usa force=True para forzar)",
                name,
            )
            return False

        for guard in list(self._switch_guards.values()):
            if not guard(name):
                logger.warning("B7: switch a '%s' vetado por switch_guard", name)
                return False

        async with self._get_switch_lock():
            return await self._do_switch_workspace(name, retries)

    async def _do_switch_workspace(self, name: str, retries: int) -> bool:

        from core.database import get_async_schema

        while retries >= 0:
            if retries == 0 and name != DEFAULT_WORKSPACE_NAME:
                name = DEFAULT_WORKSPACE_NAME

            previous_schema = get_async_schema()
            schema_switched = False
            try:
                await create_schema(name)
                await create_tables_in_schema(name)
                await set_async_schema(name)
                schema_switched = True

                await memory.switch_workspace(name)

                from agents.loader import load_workspace_agents, unload_workspace_agents
                from core.path_resolver import paths

                # Sincroniza templates de agentes y workflows (aditivo: solo
                # copia los que faltan; p.ej. un tdd.yaml nuevo aparece en
                # workspaces existentes).
                if self._workspace_is_isolated(name):
                    # Workspace producto (manifest isolate:true): SOLO lo propio
                    # + núcleo; prune de heredados de installs previos.
                    self.prune_inherited_templates(name)
                    self._ensure_own_template_assets(name)
                    self._bootstrap_core_agents(
                        paths.workspace_agents_dir(name), paths.templates_agents_dir()
                    )
                else:
                    agents_dir = paths.workspace_agents_dir(name)
                    self._bootstrap_workspace_agents(agents_dir, paths.templates_agents_dir())
                    workflows_dir = paths.workspace_workflows_dir(name)
                    self._bootstrap_workspace_workflows(
                        workflows_dir, paths.templates_workflows_dir()
                    )
                    # Skills procedimentales (doc skills, Tarea 5)
                    self._bootstrap_workspace_skills(
                        paths.workspace_skills_dir(name), paths.templates_skills_dir()
                    )

                hooks_dir = paths.workspace_hooks_dir(name)
                templates_hooks_dir = paths.templates_hooks_dir()
                if not hooks_dir.exists() or not any(hooks_dir.iterdir()):
                    self._bootstrap_workspace_hooks(hooks_dir, templates_hooks_dir)

                # Bots template-first: catálogo global aditivo +
                # sincronización YAML→DB + export de bots huérfanos (migración
                # de bots pre-plantilla). Corre en TODOS los workspaces: los
                # bots son machine-local, no assets de producto.
                self._bootstrap_workspace_bots(
                    paths.workspace_bots_dir(name), paths.templates_bots_dir()
                )
                from core.bot_templates import bootstrap_workspace_bots

                await bootstrap_workspace_bots(name)

                unload_workspace_agents()
                load_workspace_agents(name)

                from tools.loader import load_workspace_tools, unload_workspace_tools

                unload_workspace_tools()
                load_workspace_tools(name)

                from core.hook_loader import load_workspace_hooks, unload_workspace_hooks

                unload_workspace_hooks()
                load_workspace_hooks(name)

                from core.mcp.client import connect_mcp_servers, disconnect_mcp_servers

                await disconnect_mcp_servers()
                await connect_mcp_servers(name)

                self.current = name
                switch_workflow_state(name)

                logger.info(f"Switched to {name}")
                return True
            except Exception as e:
                # si la BD ya cambió de schema pero falló el resto
                # (agents/tools/hooks/MCP), restaurar el schema ANTERIOR para no
                # dejar una ventana de estado inconsistente.
                if schema_switched and previous_schema and previous_schema != name:
                    try:
                        from core.database import set_async_schema as _set

                        await _set(previous_schema)
                        logger.warning(
                            "Schema restaurado a '%s' tras fallo de switch", previous_schema
                        )
                    except Exception:
                        logger.error("No se pudo restaurar el schema previo", exc_info=True)

                retries -= 1
                if name != DEFAULT_WORKSPACE_NAME:
                    logger.warning(f"Fallback a '{DEFAULT_WORKSPACE_NAME}' desde '{name}': {e}")
                    name = DEFAULT_WORKSPACE_NAME
                    retries = max(retries, 0)
                else:
                    logger.critical(
                        f"switch_workspace('{DEFAULT_WORKSPACE_NAME}') falló: {e}", exc_info=True
                    )
                    return False

        return False

    async def create_workspace(self, name: str) -> bool:
        """Crea el workspace (si no existe) y cambia a él."""
        return await self.switch_workspace(name)

    async def delete_workspace(self, name: str) -> bool:
        # un drop con workflows en ejecución destruye la BD bajo sus pies
        # (el schema-binding apunta sesiones al workspace del workflow).
        if workflow_running():
            logger.error("delete_workspace('%s'): hay workflows activos — abortando", name)
            return False
        name = self._validate_workspace_name(name)
        if name == self.current:
            escaped = await self.switch_workspace(DEFAULT_WORKSPACE_NAME)
            if not escaped or self.current == name:
                # abortar — drop con workflow/agentes aún montados es destructivo
                logger.error(
                    "delete_workspace('%s'): el switch de escape a '%s' falló — abortando",
                    name,
                    DEFAULT_WORKSPACE_NAME,
                )
                return False
        try:
            await drop_schema(name)
            logger.info(f"Deleted {name}")
            return True
        except Exception as e:
            logger.error(f"Delete error: {e}")
            return False

    @staticmethod
    def _validate_workspace_name(name: str) -> str:
        import re

        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            raise ValueError(
                f"Nombre de workspace inválido: '{name}'. "
                "Solo se permiten minúsculas, números y guiones bajos, empezando con letra."
            )
        return name

    @staticmethod
    def _bootstrap_pattern(
        dest_dir, templates_dir, glob_pattern: str, label: str, skip_underscore: bool = False
    ):
        """Copia los templates que coinciden con glob a un workspace nuevo.

        Compartido por el bootstrap de agents/workflows/hooks (idéntico salvo
        el patrón de archivos). Aditivo: solo copia archivos que faltan.
        `skip_underscore=True` excluye plantillas `_`-prefijadas (p.ej.
        `_FULL_TEMPLATE.yaml` en workflows).
        """
        import shutil

        if not templates_dir.exists():
            logger.info("No hay templates de %s disponibles", label)
            return

        dest_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for template_file in templates_dir.glob(glob_pattern):
            if skip_underscore and template_file.name.startswith("_"):
                continue
            dest = dest_dir / template_file.name
            if not dest.exists():
                shutil.copy2(template_file, dest)
                copied += 1

        if copied:
            logger.info("Bootstrap: %d %s copiados a %s", copied, label, dest_dir)
        else:
            logger.info("%s ya existen en %s, sin cambios", label.capitalize(), dest_dir)

    @staticmethod
    def _bootstrap_workspace_agents(agents_dir, templates_dir):
        """Copia los templates de agentes a un workspace nuevo.

        Excluye `_`-prefijadas (p.ej. `_FULL_TEMPLATE.yaml` es documentación).
        """
        Workspaces._bootstrap_pattern(
            agents_dir, templates_dir, "*.yaml", "agentes", skip_underscore=True
        )

    @staticmethod
    def _bootstrap_workspace_workflows(workflows_dir, templates_dir):
        """Copia los templates de workflows a un workspace nuevo.

        Excluye `_FULL_TEMPLATE.yaml` (plantilla de documentación, no un workflow).
        """
        Workspaces._bootstrap_pattern(
            workflows_dir, templates_dir, "*.yaml", "workflows", skip_underscore=True
        )

    @staticmethod
    def _bootstrap_workspace_hooks(hooks_dir, templates_dir):
        """Copia los templates de hooks a un workspace nuevo."""
        Workspaces._bootstrap_pattern(hooks_dir, templates_dir, "*.py", "hooks")

    @staticmethod
    def _bootstrap_workspace_bots(bots_dir, templates_dir):
        """Copia el catálogo de plantillas de bots a un workspace (aditivo).

        Excluye `_`-prefijadas por la convención común de plantillas.
        """
        Workspaces._bootstrap_pattern(
            bots_dir, templates_dir, "*.yaml", "bots", skip_underscore=True
        )

    _CORE_AGENTS = ("conversacional",)

    @staticmethod
    def _load_workspace_manifest() -> dict[str, dict]:
        """Lee templates/workspaces.yaml → {name: entry}. Sin manifest → {}."""
        import yaml

        from core.path_resolver import paths

        try:
            data = (
                yaml.safe_load(
                    (paths.templates_dir() / "workspaces.yaml").read_text(encoding="utf-8")
                )
                or {}
            )
        except OSError:
            return {}
        except Exception:
            logger.warning("Manifest de workspaces inválido", exc_info=True)
            return {}
        out: dict[str, dict] = {}
        for entry in data.get("workspaces") or []:
            if isinstance(entry, dict) and entry.get("name"):
                out[str(entry["name"])] = entry
        return out

    @classmethod
    def _workspace_is_isolated(cls, name: str) -> bool:
        """Aislamiento opt-in: entrada del manifest con isolate:true."""
        return bool(cls._load_workspace_manifest().get(name, {}).get("isolate"))

    @staticmethod
    def _bootstrap_core_agents(agents_dir, templates_agents_dir):
        """Copia SOLO el set núcleo (fallback del chat simple) en aislados."""
        import shutil

        agents_dir.mkdir(parents=True, exist_ok=True)
        for agent in Workspaces._CORE_AGENTS:
            src = templates_agents_dir / f"{agent}.yaml"
            dst = agents_dir / f"{agent}.yaml"
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)

    @staticmethod
    def _ensure_own_template_assets(name: str) -> None:
        """Copia aditiva de los assets PROPIOS (templates/workspaces/<n>/)."""
        import shutil

        from core.path_resolver import paths

        src_root = paths.templates_dir() / "workspaces" / name
        if not src_root.is_dir():
            return
        for kind in ("agents", "workflows"):
            src_sub = src_root / kind
            if not src_sub.is_dir():
                continue
            dst_sub = paths.workspace_dir(name) / kind
            dst_sub.mkdir(parents=True, exist_ok=True)
            for f in sorted(src_sub.iterdir()):
                if f.name.startswith("_") or not f.is_file():
                    continue
                dst = dst_sub / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)

    @classmethod
    def prune_inherited_templates(cls, name: str) -> list[str]:
        """Borra archivos heredados de los templates globales en un workspace.

        Solo actúa sobre workspaces AISLADOS. Conserva: lo propio de
        templates/workspaces/<name>/{agents,workflows} y cualquier YAML que no
        sea un template global (p.ej. añadidos por el usuario). Idempotente.
        Retorna las rutas eliminadas.
        """
        if not cls._workspace_is_isolated(name):
            return []

        from core.path_resolver import paths

        removed: list[str] = []
        own_root = paths.templates_dir() / "workspaces" / name
        for kind, global_dir in (
            ("agents", paths.templates_agents_dir()),
            ("workflows", paths.templates_workflows_dir()),
        ):
            keep: set[str] = set()
            own_sub = own_root / kind
            if own_sub.is_dir():
                keep |= {f.name for f in own_sub.glob("*.yaml")}
            inherited: set[str] = {"_FULL_TEMPLATE.yaml"}
            if global_dir.is_dir():
                inherited |= {f.name for f in global_dir.glob("*.yaml")}
            target = paths.workspace_dir(name) / kind
            if not target.is_dir():
                continue
            for f in sorted(target.iterdir()):
                if f.name in keep or f.name not in inherited or not f.is_file():
                    continue
                f.unlink()
                removed.append(str(f))
        if removed:
            logger.info("Prune '%s': %d archivo(s) heredado(s) eliminado(s)", name, len(removed))
        return removed

    @staticmethod
    def _bootstrap_workspace_skills(skills_dir, templates_skills_dir):
        """Tarea 5 (doc skills): copia skills globales (<n>/SKILL.md) al workspace.

        Aditivo e idempotente: solo copia directorios de skill que faltan
        (precedencia workspace > global en core/skills.py).
        """
        import shutil

        if not templates_skills_dir.exists():
            return
        skills_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for skill_dir in templates_skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
                continue
            dest = skills_dir / skill_dir.name
            if not dest.exists():
                shutil.copytree(skill_dir, dest)
                copied += 1
        if copied:
            logger.info("Bootstrap: %d skills copiadas a %s", copied, skills_dir)

    @staticmethod
    def provision_template_workspaces() -> list[str]:
        """Copia aditiva de templates/workspaces/<n>/ a workspaces/<n>/.

        Lee el manifest templates/workspaces.yaml y respeta el setting
        AUTO_PROVISION_WORKSPACES (default False). Idempotente: nunca
        sobreescribe archivos existentes. El schema PostgreSQL NO se crea
        aquí — ocurre en el primer switch. Retorna nombres provisionados.
        """
        from core.config import settings

        if not getattr(settings, "auto_provision_workspaces", False):
            return []

        import shutil

        import yaml

        from core.path_resolver import paths

        manifest_path = paths.templates_dir() / "workspaces.yaml"
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except OSError:
            logger.info("Sin templates/workspaces.yaml — provisioning omitido")
            return []
        except Exception:
            logger.warning("Manifest de workspaces inválido", exc_info=True)
            return []

        provisioned: list[str] = []
        for entry in data.get("workspaces") or []:
            name = str(entry.get("name", "")) if isinstance(entry, dict) else ""
            try:
                name = Workspaces._validate_workspace_name(name)
            except ValueError:
                logger.warning(f"Provisioning: nombre inválido '{name}' omitido")
                continue
            src_root = paths.templates_dir() / "workspaces" / name
            if not src_root.is_dir():
                continue
            dst_root = paths.workspace_dir(name)
            dst_root.mkdir(parents=True, exist_ok=True)
            for sub in ("agents", "workflows", "tools", "hooks"):
                src_sub = src_root / sub
                if not src_sub.is_dir():
                    continue
                dst_sub = dst_root / sub
                dst_sub.mkdir(parents=True, exist_ok=True)
                for f in sorted(src_sub.iterdir()):
                    if f.name.startswith("_") or not f.is_file():
                        continue
                    dest_file = dst_sub / f.name
                    if dest_file.exists():
                        continue  # aditivo: no sobreescribe
                    shutil.copy2(f, dest_file)
            if Workspaces._workspace_is_isolated(name):
                Workspaces.prune_inherited_templates(name)
            provisioned.append(name)
            logger.info(f"Provisioning: workspace '{name}' asegurado desde templates")
        return provisioned


workspaces_instance = Workspaces()


def get_global_workspaces():
    return workspaces_instance


async def switch_workspace_handler(name: str) -> bool:
    return await workspaces_instance.switch_workspace(name)
