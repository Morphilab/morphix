# core/utils.py
"""
Utilidades generales de Morphix
"""

import logging
import os
import re

logger = logging.getLogger(__name__)


from typing import Any


def is_trivial_profile(profile: dict | None) -> bool:
    """Perfil trivial: sin datos útiles más allá del nombre (o vacío).

    Los perfiles auto-inferidos con solo un nombre (ej. {"name": "ChatGPT"})
    contaminan las tareas: el agente ancla su salida en ese dato stale. Compartido por loop.py (inyección),
    finalizer.py (inferencia) y memory/manager.py (persistencia).
    """
    if not profile:
        return True
    meaningful = {k: v for k, v in profile.items() if v}
    return set(meaningful) <= {"name"}


def clean_llm_response(response: Any) -> str:
    """Limpieza ULTRA-agresiva de respuestas del LLM.
    Versión canónica — fusiona la detección de coroutine (memory/manager.py)
    y el regex eval_count (workflow_utils.py)."""
    if hasattr(response, "__await__"):
        logger.error("⚠️ Se detectó un coroutine sin await en clean_llm_response")
        return "[ERROR INTERNO: llamada async sin await]"

    try:
        if hasattr(response, "choices") and response.choices:
            content = response.choices[0].message.content.strip()
        elif hasattr(response, "message") and hasattr(response.message, "content"):
            content = response.message.content.strip()
        else:
            content = str(response)

        # '': el patrón repr SÓLO aplica cuando el texto ES un repr (model=
        # al inicio); sin DOTALL — antes borraba texto legítimo entre medias.
        if content.lstrip().startswith("model="):
            content = re.sub(r"^model=.{0,200}?(?=content=)", "", content, flags=re.IGNORECASE)
        content = re.sub(r"created_at=.*?(?=\n|$)", "", content, flags=re.DOTALL)
        content = re.sub(r"thinking=.*?(?=\n|$)", "", content, flags=re.DOTALL)
        content = re.sub(r"total_duration=.*?(?=\n|$)", "", content, flags=re.DOTALL)
        content = re.sub(r"eval_count=.*?(?=\n|$)", "", content, flags=re.DOTALL)

        match = re.search(r"content='([\s\S]*?)'", content)
        if match:
            content = match.group(1).strip()

        return content.strip()
    except (AttributeError, TypeError, KeyError):
        return str(response)[:800]


def keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    """Match por palabra completa (soporta acentos) — M7/M13.

    Evita falsos positivos de substring: 'code'→'decode', 'fix'→'prefix',
    'flow'→'workflow'. El keyword debe INICIAR una palabra pero se permiten
    sufijos ('implement' matchea 'implementa', necesario en español).
    Keywords multi-palabra ('error en') también funcionan.
    """
    import re as _re

    lowered = text.lower()
    hits: list[str] = []
    for kw in keywords:
        pattern = r"(?<!\w)" + _re.escape(kw.lower()) + r"\w*"
        if _re.search(pattern, lowered):
            hits.append(kw)
    return hits


def file_write_succeeded(result: object) -> bool:
    """Éxito REAL de FileManager.write — sin falsos positivos.

    Éxito: 'Archivo <path> escrito correctamente.' (con o sin ✅).
    Un error que CONTIENE la palabra 'Archivo' ya no cuenta como éxito.
    """
    if not isinstance(result, str):
        return False
    s = result.strip()
    return "escrito correctamente" in s.lower() and not s.startswith("❌")


_ENV_PLACEHOLDER_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


def expand_env_path(path: str) -> str:
    """Expande ${VAR}, ${VAR:-default} y ~ en rutas declaradas en YAML.

    Portabilidad: los project.root de templates/workspaces/ no
    deben hardcodear /home/<usuario>. Si VAR no está seteada y hay default,
    se usa el default; sin default, el placeholder se elimina.
    """
    import os

    def _replace(match: "re.Match[str]") -> str:
        value = os.environ.get(match.group("name"))
        if value:
            return value
        default = match.group("default")
        return default if default is not None else ""

    return os.path.expanduser(_ENV_PLACEHOLDER_RE.sub(_replace, path))


_CHILD_ENV_ALLOW = frozenset({"PATH", "HOME", "LANG", "TERM", "TMPDIR", "USER", "SHELL"})


def build_child_env(home: str | None = None) -> dict[str, str]:
    """Entorno ALLOWLIST para procesos hijos (bash/MCP/git) — anti C1.

    Solo pasa variables inocuas; jamás hereda secretos (*_API_KEY,
    DATABASE_URL, ENCRYPTION_KEY...) aunque existan en os.environ.
    PATH configurable vía MORPHIX_CHILD_ENV_PATH (p.ej. para incluir
    un venv o ~/.local/bin); default conservador inalterado.
    """
    env = {k: v for k, v in os.environ.items() if k in _CHILD_ENV_ALLOW or k.startswith("LC_")}
    env["PATH"] = os.environ.get("MORPHIX_CHILD_ENV_PATH", "/usr/local/bin:/usr/bin:/bin")
    if home is not None:
        env["HOME"] = home
    return env
