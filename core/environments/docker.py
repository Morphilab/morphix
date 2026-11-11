# core/environments/docker.py — backend Docker con argv puro y testeable
"""Ejecución contenerizada: imagen pinneada por settings, mount del cwd al
MISMO path (visibilidad resuelta en el mount), red apagada
por defecto y caps de recursos. El argv se construye en :meth:`build_argv`
(puro) y el spawn es inyectable para tests sin docker real.

Si el binario ``docker`` no existe, :meth:`run` falla con error claro —
JAMÁS degrada en silencio a local (el llamante pidió contención)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from core.environments.base import BaseEnvironment, ExecResult, register_backend
from core.utils import build_child_env

logger = logging.getLogger(__name__)


@register_backend
class DockerEnvironment(BaseEnvironment):
    name = "docker"

    def __init__(
        self,
        *,
        image: str | None = None,
        network: bool | None = None,
        memory_mb: int = 2048,
        cpus: float = 1.0,
        spawn=None,
    ):
        self.image = image
        self.network = network
        self.memory_mb = int(memory_mb)
        self.cpus = float(cpus)
        # spawn inyectable: async (argv, env, timeout) -> (proc)
        self._spawn = spawn or self._spawn_default

    def _resolve(self) -> tuple[str, bool]:
        from core.config import settings

        return (
            self.image or settings.execution_docker_image,
            self.network if self.network is not None else settings.execution_docker_network,
        )

    def build_argv(
        self,
        command: str | list[str],
        *,
        cwd: str | Path,
        env_extra: dict[str, str] | None = None,
    ) -> list[str]:
        """Argumentos `docker run` completos — función pura (testeable)."""
        image, network = self._resolve()
        work_dir = str(Path(cwd).resolve())
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            f"{self.cpus}",
            "-v",
            f"{work_dir}:{work_dir}",
            "--workdir",
            work_dir,
        ]
        if not network:
            argv.append("--network")
            argv.append("none")
        for key, value in (env_extra or {}).items():
            argv += ["-e", f"{key}={value}"]
        argv.append(image)
        if isinstance(command, str):
            argv += ["/bin/bash", "-c", command]
        else:
            argv += list(command)
        return argv

    async def run(
        self,
        command: str | list[str],
        *,
        cwd: str | Path,
        timeout: float = 30.0,
        env_extra: dict[str, str] | None = None,
        home: str | None = None,
    ) -> ExecResult:
        if shutil.which("docker") is None:
            raise RuntimeError(
                "backend 'docker' seleccionado pero el binario docker no está "
                "en PATH — instala Docker o usa el backend 'local'"
            )
        argv = self.build_argv(command, cwd=cwd, env_extra=env_extra)
        env = build_child_env(home=home)
        try:
            proc = await self._spawn(argv, env, str(Path(cwd).resolve()))
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            from core.environments.base import terminate_group_graceful

            await terminate_group_graceful(proc, grace_s=_grace_seconds())
            return ExecResult(
                exit_code=-1,
                output=f"⏱️ Timeout: command exceeded {timeout}s.",
                success=False,
                timed_out=True,
                backend=self.name,
            )
        exit_code = proc.returncode or 0
        return ExecResult(
            exit_code=exit_code,
            output=ExecResult.capped(
                stdout.decode(errors="replace"), stderr.decode(errors="replace")
            ),
            success=exit_code == 0,
            backend=self.name,
        )

    @staticmethod
    async def _spawn_default(argv: list[str], env: dict[str, str], cwd: str):
        return await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )


def _grace_seconds() -> float:
    from core.config import settings

    return float(settings.execution_term_grace_seconds)
