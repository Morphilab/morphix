# core/environments/local.py — backend local con env-allowlist y teardown graceful
"""Ejecución local: reusa ``build_child_env`` (anti-secretos) y el teardown
escalonado. Es el backend por defecto y el que ``bash_manager`` usa como
semántica de referencia."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.environments.base import BaseEnvironment, ExecResult, register_backend
from core.utils import build_child_env

logger = logging.getLogger(__name__)


@register_backend
class LocalEnvironment(BaseEnvironment):
    name = "local"

    async def run(
        self,
        command: str | list[str],
        *,
        cwd: str | Path,
        timeout: float = 30.0,
        env_extra: dict[str, str] | None = None,
        home: str | None = None,
    ) -> ExecResult:
        from core.environments.base import terminate_group_graceful

        argv = ["/bin/bash", "-c", command] if isinstance(command, str) else list(command)
        work_dir = str(Path(cwd))
        env = build_child_env(home=home if home is not None else work_dir)
        if env_extra:
            env.update(env_extra)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            # Mensaje claro, sin drain post-cancelación: tras cancelar el
            # communicate() los readers ya no garantizan el buffer (contrato
            # bash_manager: mensaje de timeout estable).
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


def _grace_seconds() -> float:
    from core.config import settings

    return float(settings.execution_term_grace_seconds)
