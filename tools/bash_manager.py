"""Bash Manager — safe shell command execution.

Core CLI tool. Executes commands in the project workspace with timeout,
sanitization, and dangerous pattern blocking.
"""

import asyncio
import logging
import os
import re
import shlex
import signal
from pathlib import Path

from agents.audit import log_operation, redact_credentials
from core.config import settings
from core.path_resolver import paths
from core.utils import build_child_env
from tools.registry import tools_registry

logger = logging.getLogger(__name__)

# cap de salida capturada por stream (evita OOM del host con
# comandos verbosos; test_runner usa 4000 chars, bash 512KB).
MAX_CAPTURE_BYTES = 512 * 1024


def _cap_capture(data: bytes) -> str:
    """Decodifica y trunca a MAX_CAPTURE_BYTES con marca visible."""
    if len(data) > MAX_CAPTURE_BYTES:
        return data[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace") + "\n[…truncado]"
    return data.decode("utf-8", errors="replace")


# Blocked command patterns for security
FORBIDDEN_PATTERNS: list[str] = [
    r"\$\(.*\)",  # command substitution $(...)
    r"`[^`]+`",  # backtick command substitution
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\*",  # rm -rf /*
    r"rm\s+-rf\s+\*",  # rm -rf *
    r"rm\s+-rf\s+~",
    r"\brm\s+(-[a-zA-Z]+\s+)*/",  # rm [flags combinados] /  (fr, fR, etc.)
    r"\brm\s+(-[a-zA-Z]+\s+)*\*",  # rm [flags combinados] *
    r"\brm\s+(-[a-zA-Z]+\s+)*~",  # rm [flags combinados] ~
    r"\brm\s+(-[a-zA-Z]+\s+)*(--[a-z-]+\s+)*/",  # rm -rf --no-preserve-root /
    r"dd\s+if=",
    r"mkfs\.",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;:",  # fork bomb
    r">\s*/dev/sd[a-z]",
    r"chmod\s+777\s+/",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+.*\s+/",
    r"sudo\s+",
    r"wget\s+.*\s*-O\s+/",
    r"curl\s+.*\s*-o\s+/",
    r"^\s*>\s*/dev/null",
    r">\s*/etc/",
    r";\s*rm\s+",
    r"&&\s*rm\s+",
    r"\|\|\s*rm\s+",
    r"\bnc\s+-[nlpe]",  # netcat reverse shells
    r"\bncat\s+-[nlpe]",
    r"\bsocat\s+",
    r"\btelnet\s+",
    r"\beval\s+",
    r"\bexec\s+",
    r"\bsource\s+",
    r"curl\s+-d\s+@",  # curl data exfiltration
    r"curl\b[^|;&\n]*\s+-f\s+\S*@",  # curl -F multipart exfiltration (match sobre lowercase)
    r"curl\b[^|;&\n]*\s+--form\s+\S*@",  # curl --form exfiltration
    r"wget\s+https?",
    r"base64\s+-d.*\|.*(?:sh|bash|zsh|dash|ksh)",  # base64 decode pipe to shell
    # pipe/download-exec de payloads remotos (input llega lowercased)
    r"(?:curl|wget)\b[^\n]*\|\s*(?:[^|\n]*\|)*\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b",  # pipe-to-shell (multi-pipe)
    r"(?:curl|wget)\b[^\n]*(?:&&|;)\s*(?:[^;&\n]*(?:&&|;)\s*)*(?:sudo\s+)?(?:ba|z|da|k)?sh\b",  # download && exec (multi-op)
    r"curl\b[^|;&\n]*\s+(?:-t|--upload-file)\b",  # upload/exfil (-t: input llega lowercased)
    r"\b(?:ssh|scp|sftp)\b",  # remote exec/transfer
    r">\s*>?\s*(?:/[\w./-]*/)?workspaces/[^/\s]+/(?:tools|agents|skills)/",  # backdoor persistente
    # process substitution: `bash <(curl …)` / `. <(curl …)` ejecutan payload
    # remoto sin pipe ni archivo local. Bloqueo GENÉRICO deliberado (paridad
    # con $( y backticks): no tiene uso legítimo común aquí; grep de
    # pre-vuelo confirmó cero usos.
    r"<\(",
    r"\b(?:tee|cp)\b[^;\n|]*(?:/[\w./-]*/)?workspaces/[^/\s]+/(?:tools|agents|skills)/",  # persistencia sin redirect
    r"\brsync\b[^;\n|&]*(?:[a-z0-9._-]+@)?[\w.-]+:",  # rsync a host remoto (user@host:)
    # Extended patterns for stronger defense
    r"python\d*\s+-c\s+",  # python -c (arbitrary code)
    r"perl\s+-[eE]\s+",  # perl -e (arbitrary code)
    r"ruby\s+-[eE]\s+",  # ruby -e (arbitrary code)
    r"/dev/tcp/",  # bash /dev/tcp reverse shell
    r"\bnohup\s+",  # nohup bypass
    r"\bdisown\s+",  # disown bypass
    r"\bsetsid\s+",  # setsid bypass
    r"\bchroot\s+",  # chroot
    r"\bunshare\s+",  # namespace escape
    r"\bsystemctl\s+",  # systemd control
    r"\bmount\s+",  # mount
    r"\bumount\s+",  # umount
    # rm con rutas relativas ascendentes (../../ borra la raíz del repo)
    r"\brm\s+(-[a-zA-Z]+\s+)*\.{1,2}(\/|\s|$)",
    # xargs hacia rm/shells destructivos
    r"\bxargs\s+(rm|sh|bash|zsh|dash)\b",
    # background & terminal — genera huérfanos fuera del alcance de killpg en timeout
    r"(?<!&)&\s*$",
    # gestores de paquetes = egress de red + ejecución
    # remota de código (setup.py/scripts post-install) — contradecía la intención
    # download&&exec del resto de la blocklist.
    r"\bpip3?(?:\.\d+)?\s+install\b",
    r"\bpipx\s+install\b",
    r"\bnpm\s+(?:install|i|add)\b",
    r"\bnpx\b",
    r"\byarn\s+(?:add|install)\b",
    r"\buv\s+(?:pip\s+)?install\b",
    # awk system() = exec de shell arbitrario oculto dentro de un comando permitido
    r"\bawk\b[^;\n|]*\bsystem\s*\(",
    # heredoc de python = código arbitrario FUERA del sandbox code_exec en 1 paso
    # (el blocklist de `python -c` era bypaseable con `python3 - <<EOF`).
    r"\bpython\d*\s+-?\s*<<",
]


# Command patterns with hallucinated absolute paths
HALLUCINATED_PATHS: list[str] = [
    "/root/workspace",
    "/root/.openclaw",
    "/root/project",
    "/home/user/workspace",
]


def _sanitize_command(command: str) -> tuple[bool, str]:
    """Validate that the command is safe. Returns (is_safe, reason)."""
    if not command or not command.strip():
        return False, "Empty command"

    if len(command) > 4000:
        return False, "Command too long (max 4000 characters)"

    cmd_lower = command.lower()

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, cmd_lower):
            reason = f"Command blocked for security: pattern '{pattern}'"
            if "python" in pattern and "-c" in pattern:
                reason = (
                    "❌ 'python3 -c' está bloqueado por seguridad. Alternativas:\n"
                    "  1. Escribe el código en un archivo .py con la herramienta file_manager\n"
                    "  2. Ejecuta el archivo con bash_manager: 'python3 script.py'\n"
                    "  3. Usa test_runner si necesitas ejecutar tests"
                )
            return False, reason

    for hpath in HALLUCINATED_PATHS:
        if hpath in command:
            return False, (
                f"❌ Path absoluto '{hpath}' no es válido en este workspace.\n"
                "Usa paths relativos al project root. El directorio de trabajo YA ES el project root.\n"
                "Ejemplo: 'python3 script.py' en vez de 'cd /root/workspace && python3 script.py'"
            )

    # Validate multi-command chains segment by segment
    if any(c in command for c in (";", "&&", "||", "|")):
        segments = re.split(r"\s*[;&|]+\s*", command)
        segments = [s.strip() for s in segments if s.strip()]
        if len(segments) > 1:
            for seg in segments:
                is_safe, reason = _sanitize_command(seg)
                if not is_safe:
                    return False, f"Blocked segment '{seg[:60]}': {reason}"

    return True, ""


# Regex para redirecciones; los verbos de archivo se tokenizan por segmento
# (el patrón captura el TARGET de escritura: `tee -a /etc/passwd` debe
# chequear '/etc/passwd', no '-a'; `cp -f src /dst` chequea '/dst').
_FS_REDIRECT_PATTERN = re.compile(r"(?:>>?|\b2>)\s*(\S+)")
_DD_OF_PATTERN = re.compile(r"\bof=(\S+)")
# verbo → qué tokens del argumento son targets de ESCRITURA
# all    = todos los no-flag (tee/truncate/touch/mkdir/rmdir/rm escriben cada arg)
# last   = solo el último no-flag (cp/mv/install/ln/sed -i: el DESTINO)
_FS_WRITE_VERBS: dict[str, str] = {
    "tee": "all",
    "touch": "all",
    "mkdir": "all",
    "rmdir": "all",
    "rm": "all",
    "truncate": "all",
    "cp": "last",
    "mv": "last",
    "install": "last",
    "ln": "last",
    "sed": "last",
}


def _fs_write_targets(command: str) -> list[str]:
    """Extrae candidatos a target de escritura de un comando bash.

    Heurístico por diseño: redirecciones (regex), ``dd of=`` y verbos de
    archivo tokenizados por segmento ignorando flags."""
    targets: list[str] = []
    for pattern in (_FS_REDIRECT_PATTERN, _DD_OF_PATTERN):
        targets.extend(m.group(1) for m in pattern.finditer(command))
    for seg in re.split(r"\s*[;&|]+\s*", command):
        toks = seg.split()
        for i, tok in enumerate(toks):
            verb = _FS_WRITE_VERBS.get(tok.rsplit("/", 1)[-1])
            if verb is None:
                continue
            args = [t for t in toks[i + 1 :] if not t.startswith("-")]
            if verb == "last":
                args = args[-1:]
            targets.extend(args)
            break
    return targets


def _fs_fence_check(command: str, workspace: str) -> tuple[bool, str]:
    """Best-effort FS fence: bloquea escrituras fuera de las raíces escribibles.

    Escanea el comando en busca de targets de escritura (redirecciones y
    verbos de archivo), los resuelve contra el cwd del workspace y verifica
    que queden bajo raíces escribibles. Heurístico — la defensa en
    profundidad la completan FORBIDDEN_PATTERNS + cwd anclado."""
    from core.fs_fence import check_write_target

    base = paths.memory_dir(workspace).resolve()
    for cand in _fs_write_targets(command):
        cand = cand.strip("'\"")
        if not cand or cand.startswith("/dev/"):
            continue
        # la tilde la expande el SHELL del hijo con HOME=base —
        # resolverla aquí, no tratarla como ruta relativa literal.
        if cand == "~":
            target = str(base)
        elif cand.startswith("~/"):
            target = str(base / cand[2:])
        elif cand.startswith("~"):
            target = os.path.expanduser(cand)  # ~user → hogar real del sistema
        else:
            target = cand if os.path.isabs(cand) else str(base / cand)
        allowed, reason = check_write_target(target, workspace)
        if not allowed:
            return False, reason
    return True, ""


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Mata el grupo completo del proceso (start_new_session → pgid propio)."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            logger.warning("Failed to kill process; may be orphaned")


async def _bash_tool(
    command: str = "",
    workspace: str | None = None,
    cwd: str | None = None,
    timeout: int = 30,
    **kwargs,
) -> dict:
    """Execute a shell command safely.

    Args:
        command: Shell command to execute.
        workspace: Active workspace.
        cwd: Working directory relative to workspace (optional).
        timeout: Maximum timeout in seconds (default: 30).

    Returns:
        {"success": bool, "output": str, "exit_code": int}
    """
    if workspace is None:
        workspace = settings.active_workspace
    if not command or not command.strip():
        return {
            "success": False,
            "output": "❌ bash_manager requires 'command' parameter",
            "exit_code": -1,
        }
    is_safe, reason = _sanitize_command(command)
    if not is_safe:
        return {"success": False, "output": f"❌ {reason}", "exit_code": -1}

    # FS fence: contención de targets de escritura bajo raíces escribibles
    fence_ok, fence_reason = _fs_fence_check(command, workspace)
    if not fence_ok:
        return {"success": False, "output": f"❌ Acceso denegado: {fence_reason}", "exit_code": -1}

    # Auto-rewrite 'python' → 'python3' (the system only has python3)
    _original = command
    command = re.sub(r"(?<!\S)python(?=\s)", "python3", command)
    if command != _original:
        logger.info("Auto-rewrite: '%s' → '%s'", _original.split()[0], command.split()[0])

    base = paths.memory_dir(workspace)
    if cwd is None and kwargs.get("project_root"):
        cwd = kwargs["project_root"]
    work_dir = str(base / cwd) if cwd else str(base)
    work_dir = os.path.abspath(work_dir)

    # Security: ensure work_dir is within workspace
    try:
        Path(work_dir).resolve().relative_to(base.resolve())
    except ValueError:
        return {"success": False, "output": "❌ Directory outside workspace", "exit_code": -1}

    os.makedirs(work_dir, exist_ok=True)

    # allowlist estricta — el hijo nunca ve secretos del entorno
    env = build_child_env(home=str(base))

    # Log command before execution for audit trail (credenciales redactadas)
    logger.info("Executing bash command: %s", redact_credentials(shlex.quote(command))[:200])

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
            executable="/bin/bash",
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        exit_code = proc.returncode or 0

        output_parts = []
        if stdout:
            output_parts.append(_cap_capture(stdout))
        if stderr:
            output_parts.append(f"[stderr]\n{_cap_capture(stderr)}")

        output = "\n".join(output_parts).strip() or "(no output)"
        # la salida se redacta antes de devolverse al LLM/UI
        output = redact_credentials(output)
        # bounding byte-exacto con cola preservada (errores al final)
        from core.output_bounds import TOOL_OUTPUT_MAX_BYTES, bound_output

        _b = bound_output(output, TOOL_OUTPUT_MAX_BYTES, tail_bytes=2_000)
        output = _b.text

        logger.info("Bash command exit=%s: %s...", exit_code, redact_credentials(command)[:80])
        log_operation("bash_exec", command[:200], success=exit_code == 0)
        return {"success": exit_code == 0, "output": output, "exit_code": exit_code}

    except asyncio.CancelledError:
        # la cancelación del awaiter NO debe dejar el proceso vivo —
        # matar el grupo antes de propagar (el TimeoutError solo cubre timeout).
        # Edge: si la cancelación aterriza DURANTE el spawn, proc aún no existe.
        logger.warning(
            "Bash cancelado: matando grupo de proceso: %s...", redact_credentials(command)[:80]
        )
        if proc is not None:
            _kill_process_group(proc)
            try:
                await proc.wait()
            except Exception:
                pass
        raise
    except TimeoutError:
        logger.warning("Bash timeout (%ss): %s...", timeout, redact_credentials(command)[:80])
        if proc is not None:
            _kill_process_group(proc)
            try:
                await proc.wait()
            except Exception:
                logger.warning("Failed to wait for timed-out process termination")
        return {
            "success": False,
            "output": f"⏱️ Timeout: command exceeded {timeout}s.",
            "exit_code": -1,
        }
    except Exception as e:
        logger.error("Bash error: %s", redact_credentials(str(e)))
        return {
            "success": False,
            "output": redact_credentials(f"❌ Error: {e}"),
            "exit_code": -1,
        }


# Auto-register in the global tools registry
tools_registry.register("bash_manager")(_bash_tool)
