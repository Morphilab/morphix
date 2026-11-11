# tests/test_environments.py — matriz de backends, env-allowlist y teardown
"""Sin docker real: el spawn de docker es inyectable; el local corre
comandos reales (sin red). Contratos:

- tabla de backends (alta por decorador, sin elif) + factory con error
  accionable;
- local: env-allowlist anti-secretos (el hijo NUNCA ve *_API_KEY /
  DATABASE_URL aunque estén en os.environ), grupo propio y timeout con
  escalada graceful;
- docker: argv puro (imagen/mount/workdir/red/caps) y fail-fast claro si
  no hay binario — jamás degrada en silencio;
- write_file vía fs_fence: fuera de raíces escribibles → denegado.
"""

import asyncio
import os

import pytest

from core.environments import (
    DockerEnvironment,
    LocalEnvironment,
    available_backends,
    create_environment,
    terminate_group_graceful,
)

# ── Registro por tabla ──────────────────────────────────────────────────────


def test_registry_is_a_table_and_factory_resolves():
    assert set(available_backends()) >= {"local", "docker"}
    assert isinstance(create_environment("local"), LocalEnvironment)
    assert isinstance(create_environment("docker"), DockerEnvironment)
    with pytest.raises(ValueError, match="docker, local"):
        create_environment("kubernetes")


def test_register_backend_requires_name():
    from core.environments.base import BaseEnvironment, register_backend

    class SinNombre(BaseEnvironment):
        async def run(self, *a, **kw):  # pragma: no cover — jamás corre
            raise NotImplementedError

    with pytest.raises(ValueError, match="sin 'name'"):
        register_backend(SinNombre)


def test_default_backend_from_settings():
    from core.config import settings

    assert settings.execution_default_backend == "local"
    assert settings.execution_docker_image == "python:3.12-slim"
    assert settings.execution_docker_network is False


# ── Local ────────────────────────────────────────────────────────────────────


async def test_local_runs_and_captures(tmp_path):
    env = create_environment("local")
    res = await env.run("echo hola-mundo && echo err >&2", cwd=tmp_path, timeout=10)
    assert res.success is True and res.exit_code == 0
    assert "hola-mundo" in res.output and "[stderr]" in res.output
    assert res.backend == "local"


async def test_local_child_never_sees_secrets(tmp_path, monkeypatch):
    """El contrato central: allowlist, jamás os.environ.copy()."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secreto")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:pass@h/db")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "otro")
    env = create_environment("local")
    res = await env.run("env", cwd=tmp_path, timeout=10)
    assert "sk-super-secreto" not in res.output
    assert "postgresql://u:pass" not in res.output
    assert "PATH" in res.output  # lo inocuo sí pasa


async def test_local_timeout_graceful_then_kill(tmp_path):
    """El hijo ignora SIGTERM → la escalada termina con SIGKILL dentro del grace."""
    env = create_environment("local")
    # trap '' TERM ignora la señal graceful: solo muere con SIGKILL
    res = await env.run("trap '' TERM; echo empezando; sleep 30", cwd=tmp_path, timeout=1.0)
    assert res.timed_out is True
    assert res.success is False and res.exit_code == -1
    assert "Timeout" in res.output


async def test_local_argv_list_mode(tmp_path):
    env = create_environment("local")
    res = await env.run(["printf", "lista-ok"], cwd=tmp_path, timeout=10)
    assert res.success is True and "lista-ok" in res.output


async def test_read_write_through_fence(tmp_path, monkeypatch):
    import core.fs_fence as fence_mod

    ws_root = tmp_path / "ws"
    (ws_root / "main").mkdir(parents=True)
    # Parchear la costura que el fence REALMENTE consulta — no el singleton
    # paths: un parche de instancia de un test previo lo sombrea cuando la
    # suite completa corre en orden.
    monkeypatch.setattr(fence_mod, "writable_roots", lambda ws: [ws_root / ws])
    env = create_environment("local")

    inside = ws_root / "main" / "f.txt"
    res = await env.write_file(str(inside), b"hola", workspace="main")
    assert res["success"] is True
    assert await env.read_file(str(inside)) == b"hola"

    outside = tmp_path / "fuera.txt"
    denied = await env.write_file(str(outside), b"no", workspace="main")
    assert denied["success"] is False  # fs_fence manda


# ── Teardown escalonado ─────────────────────────────────────────────────────


async def test_terminate_group_graceful_on_dead_process():
    class FakeProc:
        pid = os.getpid()
        returncode = 0  # ya murió: no-op inmediato

        async def wait(self):
            return 0

    await terminate_group_graceful(FakeProc())  # no debe señalar nada


async def test_terminate_group_graceful_sigterm_first(tmp_path):
    """Un hijo con trap: recibe SIGTERM, imprime y sale — sin SIGKILL."""
    env = create_environment("local")
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-c",
        "trap 'echo limpiando; exit 0' TERM; echo listo; while :; do sleep 0.1; done",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(tmp_path),
        start_new_session=True,
    )
    await asyncio.sleep(0.3)  # dejar que arme el trap
    await terminate_group_graceful(proc, grace_s=5.0)
    stdout, _ = await proc.communicate()
    assert b"limpiando" in stdout, "el SIGTERM graceful llegó primero"
    assert proc.returncode == 0


# ── Docker (argv puro + spawn inyectable) ────────────────────────────────────


def test_docker_argv_mounts_same_path_and_network_off(tmp_path):
    env = DockerEnvironment(image="python:3.12-slim", network=False)
    argv = env.build_argv("pytest -q", cwd=tmp_path)
    assert argv[0:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path}:{tmp_path}" in argv  # mismo path dentro (visibilidad por mount)
    assert "python:3.12-slim" in argv
    i = argv.index("--network")
    assert argv[i + 1] == "none"
    assert "--memory" in argv and "--cpus" in argv
    assert argv[-1] == "pytest -q" and argv[-2] == "-c"


def test_docker_argv_env_extra_and_list_command(tmp_path):
    env = DockerEnvironment(image="img")
    argv = env.build_argv(["python", "-V"], cwd=tmp_path, env_extra={"CI": "1"})
    assert "-e" in argv and "CI=1" in argv
    assert argv[-2:] == ["python", "-V"]


def test_docker_argv_uses_settings_when_not_overridden(tmp_path):
    env = DockerEnvironment()
    argv = env.build_argv("true", cwd=tmp_path)
    assert "python:3.12-slim" in argv  # de settings.execution_docker_image


async def test_docker_missing_binary_fails_fast(tmp_path, monkeypatch):
    import core.environments.docker as docker_mod

    monkeypatch.setattr(docker_mod.shutil, "which", lambda name: None)
    env = DockerEnvironment()
    with pytest.raises(RuntimeError, match="instala Docker"):
        await env.run("echo hola", cwd=tmp_path, timeout=5)


async def test_docker_run_via_injected_spawn(tmp_path):
    """El spawn inyectable recibe el argv puro y el resultado fluye igual."""
    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"desde-contenedor", b""

    async def fake_spawn(argv, env, cwd):
        captured["argv"] = argv
        return FakeProc()

    env = DockerEnvironment(image="img-x", spawn=fake_spawn)
    res = await env.run("echo desde-contenedor", cwd=tmp_path, timeout=5)
    assert res.success is True
    assert "desde-contenedor" in res.output
    assert captured["argv"][0] == "docker"
    assert "img-x" in captured["argv"]


# ── bash_manager adopta la escalada graceful ───────────────────────────────


async def test_bash_manager_timeout_still_clean(tmp_path):
    from tools.bash_manager import _bash_tool

    res = await _bash_tool("sleep 30", timeout=1)
    assert res["success"] is False
    assert "Timeout" in res["output"]
    assert res["exit_code"] == -1
