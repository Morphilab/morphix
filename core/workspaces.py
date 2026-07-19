import asyncio
import logging

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


class Workspaces:
    def __init__(self):
        self.current = "main"
        self._switch_lock: asyncio.Lock | None = None
        self._switch_lock_loop: asyncio.AbstractEventLoop | None = None

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
        try:
            return await list_schemas()
        except Exception as e:
            logger.exception("Error listing schemas")
            return []

    async def switch_workspace(self, name: str, retries: int = 1) -> bool:
        if not name or not name.strip():
            logger.error("Empty workspace name, using 'main'")
            name = "main"
        name = self._validate_workspace_name(name)

        async with self._get_switch_lock():
            return await self._do_switch_workspace(name, retries)

    async def _do_switch_workspace(self, name: str, retries: int) -> bool:

        while retries >= 0:
            if retries == 0 and name != "main":
                name = "main"
            try:
                await create_schema(name)
                await create_tables_in_schema(name)
                await set_async_schema(name)

                await memory.switch_workspace(name)

                from agents.loader import load_workspace_agents, unload_workspace_agents
                from core.path_resolver import paths

                # Copy agent and workflow templates if workspace is new
                agents_dir = paths.workspace_agents_dir(name)
                templates_agents_dir = paths.templates_agents_dir()
                if not agents_dir.exists() or not any(agents_dir.iterdir()):
                    self._bootstrap_workspace_agents(agents_dir, templates_agents_dir)

                workflows_dir = paths.workspace_workflows_dir(name)
                templates_wf_dir = paths.templates_workflows_dir()
                if not workflows_dir.exists() or not any(workflows_dir.iterdir()):
                    self._bootstrap_workspace_workflows(workflows_dir, templates_wf_dir)

                hooks_dir = paths.workspace_hooks_dir(name)
                templates_hooks_dir = paths.templates_hooks_dir()
                if not hooks_dir.exists() or not any(hooks_dir.iterdir()):
                    self._bootstrap_workspace_hooks(hooks_dir, templates_hooks_dir)

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
                retries -= 1
                if name != "main":
                    logger.warning(f"Fallback a 'main' desde '{name}': {e}")
                    name = "main"
                    retries = max(retries, 0)
                else:
                    logger.critical(f"switch_workspace('main') falló: {e}", exc_info=True)
                    return False

        return False

    async def create_workspace(self, name: str) -> bool:
        """Crea el workspace (si no existe) y cambia a él."""
        return await self.switch_workspace(name)

    async def delete_workspace(self, name: str) -> bool:
        name = self._validate_workspace_name(name)
        if name == self.current:
            await self.switch_workspace("main")
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
    def _bootstrap_workspace_agents(agents_dir, templates_dir):
        """Copia los templates de agentes a un workspace nuevo."""
        import shutil

        if not templates_dir.exists():
            logger.info("No hay templates de agentes disponibles")
            return

        agents_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for template_file in templates_dir.glob("*.yaml"):
            dest = agents_dir / template_file.name
            if not dest.exists():
                shutil.copy2(template_file, dest)
                copied += 1

        if copied:
            logger.info(f"Bootstrap: {copied} agentes copiados a {agents_dir}")
        else:
            logger.info(f"Agentes ya existen en {agents_dir}, sin cambios")

    @staticmethod
    def _bootstrap_workspace_workflows(workflows_dir, templates_dir):
        """Copia los templates de workflows a un workspace nuevo."""
        import shutil

        if not templates_dir.exists():
            logger.info("No hay templates de workflows disponibles")
            return

        workflows_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for template_file in templates_dir.glob("*.yaml"):
            dest = workflows_dir / template_file.name
            if not dest.exists():
                shutil.copy2(template_file, dest)
                copied += 1

        if copied:
            logger.info(f"Bootstrap: {copied} workflows copiados a {workflows_dir}")
        else:
            logger.info(f"Workflows ya existen en {workflows_dir}, sin cambios")

    @staticmethod
    def _bootstrap_workspace_hooks(hooks_dir, templates_dir):
        """Copia los templates de hooks a un workspace nuevo."""
        import shutil

        if not templates_dir.exists():
            logger.info("No hay templates de hooks disponibles")
            return

        hooks_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for template_file in templates_dir.glob("*.py"):
            dest = hooks_dir / template_file.name
            if not dest.exists():
                shutil.copy2(template_file, dest)
                copied += 1

        if copied:
            logger.info(f"Bootstrap: {copied} hooks copiados a {hooks_dir}")
        else:
            logger.info(f"Hooks ya existen en {hooks_dir}, sin cambios")


workspaces_instance = Workspaces()


def get_global_workspaces():
    return workspaces_instance


async def switch_workspace_handler(name: str):
    return await workspaces_instance.switch_workspace(name)
