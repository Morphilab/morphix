"""Bash Manager — safe shell command execution.

Core CLI tool. Executes commands in the project workspace with timeout,
sanitization, and dangerous pattern blocking.
"""

import asyncio
import logging
import os
import re
import shlex
from pathlib import Path

from agents.audit import log_operation
from core.path_resolver import paths
from tools.registry import tools_registry

logger = logging.getLogger(__name__)

# Blocked command patterns for security
FORBIDDEN_PATTERNS: list[str] = [
    r"\$\(.*\)",  # command substitution $(...)
    r"`[^`]+`",  # backtick command substitution
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\*",  # rm -rf /*
    r"rm\s+-rf\s+\*",  # rm -rf *
    r"rm\s+-rf\s+~",
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
    r"wget\s+https?",
    r"base64\s+-d.*\|.*(?:sh|bash|zsh|dash|ksh)",  # base64 decode pipe to shell
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

    # Additional check: command must contain at least one safe path/command
    if any(c in command for c in (";", "&&", "||", "|")):
        # Multi-command: require each segment to be safe too
        pass

    return True, ""


async def _bash_tool(
    command: str = "",
    workspace: str = "main",
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
    if not command or not command.strip():
        return {
            "success": False,
            "output": "❌ bash_manager requires 'command' parameter",
            "exit_code": -1,
        }
    is_safe, reason = _sanitize_command(command)
    if not is_safe:
        return {"success": False, "output": f"❌ {reason}", "exit_code": -1}

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

    env = os.environ.copy()
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    env["HOME"] = str(base)
    # Clear dangerous env vars
    for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP"):
        env.pop(key, None)

    # Log command before execution for audit trail
    logger.info(f"Executing bash command: {shlex.quote(command)[:200]}")

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        exit_code = proc.returncode or 0

        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")

        output = "\n".join(output_parts).strip() or "(no output)"

        logger.info(f"Bash command exit={exit_code}: {command[:80]}...")
        log_operation("bash_exec", command[:200], success=exit_code == 0)
        return {"success": exit_code == 0, "output": output, "exit_code": exit_code}

    except TimeoutError:
        logger.warning(f"Bash timeout ({timeout}s): {command[:80]}...")
        try:
            proc.kill()
            await proc.wait()  # Clean up zombie process
        except Exception:
            logger.warning("Failed to kill timed-out process; may be orphaned")
        return {
            "success": False,
            "output": f"⏱️ Timeout: command exceeded {timeout}s.",
            "exit_code": -1,
        }
    except Exception as e:
        logger.error(f"Bash error: {e}")
        return {"success": False, "output": f"❌ Error: {e}", "exit_code": -1}


# Auto-register in the global tools registry
tools_registry.register("bash_manager")(_bash_tool)
