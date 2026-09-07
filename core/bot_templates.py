# core/bot_templates.py — plantillas de bots template-first
"""Fuente de verdad de identidad/capacidades de bots: YAML → DB, siempre.

- ``templates/bots/<slug>.yaml``    — catálogo global (provisionado aditivo).
- ``workspaces/<ws>/bots/<slug>.yaml`` — copia/override editable por workspace.

Un bot NUEVO exige plantilla: ``BotsService`` ya no es la vía de creación.
La DB (tabla ``bots``) queda como PROYECCIÓN runtime sincronizada desde el
YAML (identity fields); enabled/ui_meta/historial son runtime puro.

Validación fail-loud (patrón orchestration/loader.py): tool inexistente,
slug inválido o campo desconocido ⇒ ValueError accionable al cargar/provisionar,
nunca en el turno 3 de la madrugada.
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.bots import BotError, validate_slug
from core.path_resolver import paths

logger = logging.getLogger(__name__)

# Tools de intercepción (no viven en TOOL_DEFINITIONS pero el loop las maneja):
# send_to_bot la inyecta el loop con dm_enabled; ask_clarification se intercepta.
_RESERVED_TOOLS = {"send_to_bot", "ask_clarification"}


class BotTemplate(BaseModel):
    """Contrato completo de una plantilla de bot (YAML)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str | None = None
    description: str = ""
    soul_md: str = ""
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    tool_names: list[str] = Field(default_factory=list)
    skill_allowlist: list[str] = Field(default_factory=list)
    # Quién habla por defecto (perfil del registry); bots conversan con
    # identity layers propios — el perfil solo aporta fallback.
    agent: str = "conversacional"
    # ask_clarification permitido SOLO en chat canónico (matriz de transporte:
    # dm/salas/rutinas jamás pausan — no hay humano al otro lado).
    can_pause: bool = False
    # Protocolo inter-bot DM (send_to_bot) en el turno: False para salas/rutinas.
    dm_enabled: bool = True

    @field_validator("slug")
    @classmethod
    def _slug_valido(cls, v: str) -> str:
        return validate_slug(v)

    @field_validator("soul_md")
    @classmethod
    def _soul_no_vacio(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("soul_md vacío: un bot sin SOUL no tiene identidad — escríbelo")
        return v


def _known_tools() -> set[str]:
    """Catálogo de tools registradas + intercepción (send_to_bot/ask_clarification)."""
    try:
        from tools.specs import TOOL_DEFINITIONS

        return set(TOOL_DEFINITIONS) | _RESERVED_TOOLS
    except Exception:  # pragma: no cover — specs siempre importable en runtime real
        return set(_RESERVED_TOOLS)


def _known_skills() -> set[str]:
    try:
        from core.skills import discover_skills

        return {s.name for s in discover_skills()}
    except Exception:
        return set()


def _read_and_validate(path: Path, *, slug_override: str | None = None) -> BotTemplate:
    """Lee un YAML de bot y valida contra BotTemplate. Falla ruidoso.

    El nombre del ARCHIVO es autoritativo: el slug del YAML (si existe) debe
    coincidir con el stem o se rechaza — evita desincronía archivo↔identidad.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stem = slug_override or path.stem
    data["slug"] = stem
    try:
        template = BotTemplate.model_validate(data)
    except Exception as e:
        raise ValueError(f"Plantilla de bot inválida '{path}': {e}") from e
    _validate_capabilities(template)
    return template


def _validate_capabilities(t: BotTemplate) -> None:
    known_tools = _known_tools()
    unknown_tools = [x for x in t.tool_names if x not in known_tools]
    if unknown_tools:
        raise ValueError(
            f"Plantilla de bot '{t.slug}': tools inexistentes {sorted(unknown_tools)}. "
            f"Catálogo: {sorted(known_tools)}"
        )
    known_skills = _known_skills()
    if known_skills:
        unknown_skills = [x for x in t.skill_allowlist if x not in known_skills]
        if unknown_skills:
            raise ValueError(
                f"Plantilla de bot '{t.slug}': skills inexistentes {sorted(unknown_skills)}"
            )


def list_bot_templates(workspace_name: str | None = None) -> list[str]:
    """Nombres disponibles: unión workspace ∪ global (workspace gana)."""
    names: set[str] = set()
    if workspace_name:
        ws_dir = paths.workspace_bots_dir(workspace_name)
        if ws_dir.is_dir():
            names |= {f.stem for f in ws_dir.glob("*.yaml") if not f.name.startswith("_")}
    g_dir = paths.templates_bots_dir()
    if g_dir.is_dir():
        names |= {f.stem for f in g_dir.glob("*.yaml") if not f.name.startswith("_")}
    return sorted(names)


def load_bot_template(workspace_name: str | None, name: str) -> dict[str, Any]:
    """Carga la plantilla (workspace primero, luego global). Nunca devuelve {}.

    Returns:
        dict listo para merge sobre bot_def (incluye can_pause/dm_enabled/agent).
    """
    validate_slug(name)
    ws_path = paths.workspace_bots_dir(workspace_name or "") / f"{name}.yaml"
    g_path = paths.templates_bots_dir() / f"{name}.yaml"
    if ws_path.exists():
        t = _read_and_validate(ws_path)
    elif g_path.exists():
        t = _read_and_validate(g_path)
    else:
        raise FileNotFoundError(
            f"No existe plantilla de bot '{name}' "
            f"(ni local en workspace '{workspace_name}' ni global en {g_path.parent})"
        )
    return {
        "can_pause": t.can_pause,
        "dm_enabled": t.dm_enabled,
        "agent": t.agent,
    }


def read_bot_template_fields(workspace_name: str | None, name: str) -> dict | None:
    """Campos COMPLETOS de la plantilla para edición GUI (workspace primero).

    None si no existe archivo (ni local ni global). La DB no participa: la
    edición siempre parte del YAML (fuente de verdad).
    """
    validate_slug(name)
    ws_path = paths.workspace_bots_dir(workspace_name or "") / f"{name}.yaml"
    g_path = paths.templates_bots_dir() / f"{name}.yaml"
    path = ws_path if ws_path.exists() else (g_path if g_path.exists() else None)
    if path is None:
        return None
    return _read_and_validate(path).model_dump(exclude={"slug"})


def delete_bot_template_file(workspace_name: str | None, name: str) -> bool:
    """Elimina el YAML del workspace (delete de bot = bot sin plantilla)."""
    validate_slug(name)
    ws_path = paths.workspace_bots_dir(workspace_name or "") / f"{name}.yaml"
    if ws_path.exists():
        ws_path.unlink()
        return True
    return False


def write_bot_template(workspace_name: str | None, name: str, data: dict) -> Path:
    """Escribe/actualiza el YAML del workspace (fuente de verdad de edición GUI).

    La plantilla global es catálogo: la edición del usuario SIEMPRE vive en el
    workspace. Validación fail-loud antes de escribir.
    """
    data = {**data, "slug": name}
    template = BotTemplate.model_validate(data)
    _validate_capabilities(template)
    ws_dir = paths.workspace_bots_dir(workspace_name or "")
    ws_dir.mkdir(parents=True, exist_ok=True)
    payload = template.model_dump(exclude={"slug"})
    ws_path = ws_dir / f"{name}.yaml"
    ws_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    logger.info("Plantilla de bot escrita: %s", ws_path)
    return ws_path


# ── Sincronización YAML → DB (upsert) + export de huérfanas ──────────────


_IDENTITY_FIELDS = (
    "display_name",
    "description",
    "soul_md",
    "provider",
    "model",
    "temperature",
    "tool_names",
    "skill_allowlist",
)


def _template_to_row_payload(t: BotTemplate) -> dict:
    return {
        "display_name": t.display_name or t.slug,
        "description": t.description,
        "soul_md": t.soul_md,
        "provider": t.provider,
        "model": t.model,
        "temperature": t.temperature,
        "tool_names": list(t.tool_names),
        "skill_allowlist": list(t.skill_allowlist),
    }


def _row_to_template_payload(row: dict) -> dict:
    return {
        "slug": row["slug"],
        "display_name": row.get("display_name"),
        "description": row.get("description", ""),
        "soul_md": row.get("soul_md", ""),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "temperature": row.get("temperature"),
        "tool_names": list(row.get("tool_names") or []),
        "skill_allowlist": list(row.get("skill_allowlist") or []),
        "can_pause": False,
        "dm_enabled": True,
        "agent": "conversacional",
    }


async def export_orphan_bots(workspace_name: str) -> list[str]:
    """Bots en DB sin YAML ⇒ exporta su identidad a workspaces/<ws>/bots/.

    Migración one-shot (alfa/beta/sigma pre-template-first). Idempotente:
    solo toca slugs sin archivo. La plantilla exportada pierde can_pause
    (false — conservador) y dm_enabled true (protocolo histórico).
    """
    from core.bots import BotsService
    from core.workspaces import get_global_workspaces

    ws = workspace_name or get_global_workspaces().current
    ws_dir = paths.workspace_bots_dir(ws)
    existing_files = {f.stem for f in ws_dir.glob("*.yaml")} if ws_dir.is_dir() else set()
    exported: list[str] = []
    for row in await BotsService.list_bots(include_disabled=True):
        slug = str(row["slug"])
        if slug in existing_files:
            continue
        try:
            write_bot_template(ws, slug, _row_to_template_payload(row))
            exported.append(slug)
        except Exception as e:
            logger.warning("export de bot huérfano '%s' falló: %s", slug, e)
    if exported:
        logger.info("Bots exportados a plantillas: %s", exported)
    return exported


async def sync_bot_templates(workspace_name: str | None = None) -> dict:
    """Upsert YAML→DB para TODAS las plantillas del workspace (aditivo).

    - Plantilla nueva ⇒ fila creada (enabled=True).
    - Plantilla existente ⇒ identity fields sincronizados SI cambiaron.
    - YAML roto ⇒ warning y continúa (un YAML malo no bloquea el roster).

    Returns:
        {"created": [...], "updated": [...], "unchanged": [...], "errors": [...]}
    """
    from core.bots import BotsService
    from core.workspaces import get_global_workspaces

    ws = workspace_name or get_global_workspaces().current
    ws_dir = paths.workspace_bots_dir(ws)
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "unchanged": [],
        "errors": [],
    }
    if not ws_dir.is_dir():
        return result

    for path in sorted(ws_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        slug = path.stem
        try:
            t = _read_and_validate(path)
        except Exception as e:
            logger.warning("plantilla de bot inválida '%s' — omitida: %s", path.name, e)
            result["errors"].append(slug)
            continue
        try:
            payload = _template_to_row_payload(t)
            current = await BotsService.get_bot(t.slug)
            if current is None:
                await BotsService.create_bot(t.slug, **payload, enabled=True)
                result["created"].append(t.slug)
            else:
                diff: dict[str, Any] = {
                    k: v
                    for k, v in payload.items()
                    if k in _IDENTITY_FIELDS
                    and (
                        list(v) != list(current.get(k) or [])
                        if isinstance(v, list)
                        else v != current.get(k)
                    )
                }
                if diff:
                    await BotsService.update_bot(t.slug, **diff)
                    result["updated"].append(t.slug)
                else:
                    result["unchanged"].append(t.slug)
        except Exception as e:
            logger.warning("sync de plantilla de bot '%s' falló: %s", slug, e)
            result["errors"].append(slug)

    if result["created"] or result["updated"]:
        logger.info(
            "Bots sincronizados desde plantillas (%s): +creados %s ~actualizados %s",
            ws,
            result["created"],
            result["updated"],
        )
    return result


async def bootstrap_workspace_bots(workspace_name: str) -> None:
    """Pipeline completo: export de huérfanas → upsert YAML→DB."""
    await export_orphan_bots(workspace_name)
    await sync_bot_templates(workspace_name)


__all__ = [
    "BotTemplate",
    "list_bot_templates",
    "load_bot_template",
    "read_bot_template_fields",
    "write_bot_template",
    "delete_bot_template_file",
    "export_orphan_bots",
    "sync_bot_templates",
    "bootstrap_workspace_bots",
    "BotError",
]
