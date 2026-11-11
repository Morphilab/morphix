# core/skills_curator.py — bucle de aprendizaje: telemetría + walk + archivo
"""Curator de skills procedimentales.

Contratos:

- **Provenance manda**: solo las skills con ``created_by: agent`` en su
  frontmatter entran al walk automático. Las bundled (copias de
  ``templates/skills/`` sin esa clave) y las del catálogo son intocables.
- **Archive es el máximo**: jamás se borra; ``.archive/<name>/`` es
  restaurable con :func:`restore_skill`.
- **Pinned exento de todo**: ni stale ni archive, ni manual.
- **Uso despierta**: actividad sobre una skill ``stale`` la devuelve a
  ``active``; una ``archived`` no se toca (no es cargable).
- **Sidecar por workspace**: ``workspaces/<ws>/skills/.curator.json`` —
  mismo patrón que ``.aprobaciones.json`` (JSON + escritura atómica).
- **Revisor LLM opcional e inyectable**: el walk es determinista por edad;
  un ``reviewer`` (canal fast de ``llm.models.call``) puede VETAR archivos
  ("keep") pero nunca forzarlos. Sin red, sin reviewer: solo edad.

Estados: active → stale → archived; el uso despierta stale.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.config import settings
from core.path_resolver import PathResolver as Paths

logger = logging.getLogger(__name__)

SIDEKICK_FILE = ".curator.json"
ARCHIVE_DIR = ".archive"

# Provenance que el walk automático puede gestionar; todo lo demás es isla.
MANAGED_PROVENANCE = "agent"

SkillReviewer = Callable[[list[dict[str, Any]]], Awaitable[dict[str, str]]]


def _now() -> datetime:
    return datetime.now(UTC)


def _to_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Frontmatter mínimo clave: valor (mismo formato que core/skills.py)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not raw.startswith("---\n"):
        return {}
    end = raw.find("\n---\n", 4)
    if end < 0:
        return {}
    front: dict[str, str] = {}
    for line in raw[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            front[key.strip()] = value.strip().strip("\"'")
    return front


# ── Sidecar ─────────────────────────────────────────────────────────────────


def _sidecar_path(workspace: str) -> Path:
    return Paths.workspace_skills_dir(workspace) / SIDEKICK_FILE


def _load_store(workspace: str) -> dict[str, Any]:
    path = _sidecar_path(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"skills": {}, "last_run_at": None, "paused": False}
    data.setdefault("skills", {})
    data.setdefault("last_run_at", None)
    data.setdefault("paused", False)
    return data


def _save_store(workspace: str, data: dict[str, Any]) -> None:
    path = _sidecar_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _record(workspace: str, name: str, provenance: str, *, count_key: str | None) -> None:
    data = _load_store(workspace)
    rec = data["skills"].setdefault(
        name,
        {
            "use_count": 0,
            "view_count": 0,
            "patch_count": 0,
            "last_activity_at": None,
            "state": "active",
            "pinned": False,
            "created_by": provenance,
        },
    )
    if count_key:
        rec[count_key] = int(rec.get(count_key) or 0) + 1
    rec["last_activity_at"] = _now().isoformat()
    if rec.get("state") == "stale":
        rec["state"] = "active"  # el uso despierta; archived no se toca
    _save_store(workspace, data)


# ── API de telemetría ───────────────────────────────────────────────────────


def record_activity(workspace: str | None, name: str, kind: str = "view") -> None:
    """Registra uso de una skill en el sidecar del workspace (best-effort).

    Llamado por ``core/skills.load_skill`` — sin workspace no hay dónde
    registrar (skills globales puras no tienen perfil de uso).
    """
    if not workspace:
        return
    count_key = {"view": "view_count", "use": "use_count", "patch": "patch_count"}.get(kind)
    if count_key is None:
        raise ValueError(f"kind inválido: {kind!r} (view|use|patch)")
    skill_dir = Paths.workspace_skills_dir(workspace) / name
    provenance = _parse_frontmatter(skill_dir / "SKILL.md").get("created_by", MANAGED_PROVENANCE)
    try:
        _record(workspace, name, provenance, count_key=count_key)
    except OSError as exc:
        logger.warning("curator: no se pudo registrar actividad de %s: %s", name, exc)


def curator_status(workspace: str) -> dict[str, Any]:
    """Snapshot del sidecar + skills en disco clasificadas por provenance."""
    data = _load_store(workspace)
    skills_dir = Paths.workspace_skills_dir(workspace)
    on_disk: dict[str, str] = {}
    if skills_dir.is_dir():
        for path in sorted(skills_dir.glob("*/SKILL.md")):
            front = _parse_frontmatter(path)
            if "name" in front:
                on_disk[front["name"]] = front.get("created_by", "template")
    archived = []
    archive_root = skills_dir / ARCHIVE_DIR
    if archive_root.is_dir():
        for path in sorted(archive_root.glob("*/SKILL.md")):
            front = _parse_frontmatter(path)
            if "name" in front:
                archived.append(front["name"])
    return {
        "workspace": workspace,
        "paused": bool(data["paused"]),
        "last_run_at": data["last_run_at"],
        "records": data["skills"],
        "on_disk": on_disk,
        "archived": archived,
        "config": {
            "enabled": settings.skills_curator_enabled,
            "stale_after_days": settings.skills_curator_stale_after_days,
            "archive_after_days": settings.skills_curator_archive_after_days,
            "interval_hours": settings.skills_curator_interval_hours,
            "min_idle_hours": settings.skills_curator_min_idle_hours,
        },
    }


# ── Pausa y scheduler ───────────────────────────────────────────────────────


def set_paused(workspace: str, paused: bool) -> None:
    data = _load_store(workspace)
    data["paused"] = bool(paused)
    _save_store(workspace, data)


def is_paused(workspace: str) -> bool:
    return bool(_load_store(workspace)["paused"])


def should_run_now(workspace: str, now: datetime | None = None) -> bool:
    """Gate del scheduler: flag + no pausa + intervalo transcurrido + idle."""
    if not settings.skills_curator_enabled:
        return False
    data = _load_store(workspace)
    if data["paused"]:
        return False
    last = _to_dt(data["last_run_at"])
    now = now or _now()
    if last is not None:
        if now - last < timedelta(hours=settings.skills_curator_interval_hours):
            return False
        if now - _last_activity(data) < timedelta(hours=settings.skills_curator_min_idle_hours):
            return False
    return True


def _last_activity(data: dict[str, Any]) -> datetime:
    """Actividad más reciente del sidecar (epoch muy viejo si no hay)."""
    best: datetime | None = None
    for rec in data["skills"].values():
        ts = _to_dt(rec.get("last_activity_at"))
        if ts is not None and (best is None or ts > best):
            best = ts
    return best or datetime(1970, 1, 1, tzinfo=UTC)


# ── Walk determinista ───────────────────────────────────────────────────────


def _inactive_days(rec: dict[str, Any], now: datetime) -> float:
    ts = _to_dt(rec.get("last_activity_at"))
    if ts is None:
        return float("inf")  # sin registro de uso: candidata inmediata
    return (now - ts).total_seconds() / 86400


async def apply_automatic_transitions(
    workspace: str,
    now: datetime | None = None,
    *,
    reviewer: SkillReviewer | None = None,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Walk determinista active→stale→archived (solo created_by=agent).

    ``reviewer`` opcional recibe las candidatas a archivo con su telemetría
    y devuelve {name: "archive"|"keep"} — un veto ("keep") conserva la
    skill; sin reviewer manda solo la edad. Pinned y bundled exentos.
    """
    now = now or _now()
    data = _load_store(workspace)
    skills_dir = Paths.workspace_skills_dir(workspace)
    result: dict[str, list[str]] = {"stale": [], "archived": [], "kept": []}

    managed = _managed_skills(skills_dir, data)
    stale_days = settings.skills_curator_stale_after_days
    archive_days = settings.skills_curator_archive_after_days

    candidates: list[dict[str, Any]] = []
    for name, rec in sorted(managed.items()):
        if rec.get("pinned"):
            continue
        state = rec.get("state", "active")
        inactive = _inactive_days(rec, now)
        if state == "active" and inactive > stale_days:
            rec["state"] = "stale"
            result["stale"].append(name)
            state = "stale"
        if state == "stale" and inactive > archive_days:
            candidates.append(
                {
                    "name": name,
                    **{
                        k: rec.get(k)
                        for k in ("view_count", "use_count", "patch_count", "last_activity_at")
                    },
                }
            )

    if candidates:
        verdicts: dict[str, str] = {}
        if reviewer is not None:
            verdicts = await reviewer(candidates)
        for cand in candidates:
            name = cand["name"]
            if str(verdicts.get(name, "archive")).lower() == "keep":
                result["kept"].append(name)
                continue
            if not dry_run:
                _archive_dir(workspace, name)
                managed[name]["state"] = "archived"
            result["archived"].append(name)

    if not dry_run:
        data["last_run_at"] = now.isoformat()
        _save_store(workspace, data)
    return result


def _managed_skills(skills_dir: Path, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Registros del sidecar solo para skills created_by=agent presentes en disco."""
    managed: dict[str, dict[str, Any]] = {}
    if not skills_dir.is_dir():
        return managed
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        front = _parse_frontmatter(path)
        name = front.get("name")
        if not name or front.get("created_by") != MANAGED_PROVENANCE:
            continue
        rec = data["skills"].setdefault(
            name,
            {
                "use_count": 0,
                "view_count": 0,
                "patch_count": 0,
                "last_activity_at": None,
                "state": "active",
                "pinned": False,
                "created_by": MANAGED_PROVENANCE,
            },
        )
        managed[name] = rec
    return managed


def _archive_dir(workspace: str, name: str) -> None:
    skills_dir = Paths.workspace_skills_dir(workspace)
    src = skills_dir / name
    dst_root = skills_dir / ARCHIVE_DIR
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / name
    if dst.exists():
        # restaurable sin destruir: versiona el choque de nombres
        stamp = _now().strftime("%Y%m%d%H%M%S")
        dst = dst_root / f"{name}.{stamp}"
    src.rename(dst)


# ── Revisor LLM (canal fast, inyectable) ────────────────────────────────────


async def default_reviewer(candidates: list[dict[str, Any]]) -> dict[str, str]:
    """Pase LLM opcional: el modelo VETA ("keep") pero jamás fuerza archivos.

    Conservador por diseño: cualquier respuesta no-parseable cuenta como
    "keep" — sin red o con respuesta basura el walk determinista no pierde
    nada (la edad re-evalúa en la próxima corrida).
    """
    if not candidates:
        return {}
    listing = "\n".join(
        f"- {c['name']}: views={c.get('view_count', 0)} uses={c.get('use_count', 0)} "
        f"última={c.get('last_activity_at')}"
        for c in candidates
    )
    prompt = (
        "Para cada skill abajo responde SOLO un objeto JSON "
        '{"<name>": "archive"|"keep"} decidiendo si archivarla por '
        "inutilidad aparente (keep si tiene valor o dudas):\n\n" + listing
    )
    try:
        from llm import models

        response = await models.call(
            messages=[{"role": "user", "content": prompt}],
            role="fast",
            temperature=0.0,
            max_tokens=400,
        )
        raw = response.choices[0].message.content or "{}"
        start, end = raw.find("{"), raw.rfind("}")
        verdicts = json.loads(raw[start : end + 1]) if start >= 0 else {}
        return {str(k): str(v) for k, v in verdicts.items()}
    except Exception as exc:  # noqa: BLE001 — reviewer falla-open a "keep"
        logger.warning("curator: reviewer no disponible (%s); todo queda en keep", exc)
        return {c["name"]: "keep" for c in candidates}


# ── Acciones manuales ──────────────────────────────────────────────────────


def _require_record(workspace: str, name: str) -> dict[str, Any]:
    data = _load_store(workspace)
    rec = data["skills"].get(name)
    if rec is None:
        raise KeyError(f"skill '{name}' sin registro en el curator")
    return rec


def set_pinned(workspace: str, name: str, pinned: bool) -> None:
    data = _load_store(workspace)
    rec = data["skills"].get(name)
    if rec is None:
        raise KeyError(f"skill '{name}' sin registro en el curator")
    rec["pinned"] = bool(pinned)
    _save_store(workspace, data)


def archive_skill(workspace: str, name: str) -> Path:
    """Archivo manual (cualquier provenance salvo pinned) — explícito > auto."""
    rec = _require_record(workspace, name)
    if rec.get("pinned"):
        raise PermissionError(f"'{name}' está pinned: quita el pin primero")
    _archive_dir(workspace, name)
    data = _load_store(workspace)
    data["skills"].setdefault(name, {"created_by": "template"})["state"] = "archived"
    _save_store(workspace, data)
    return Paths.workspace_skills_dir(workspace) / ARCHIVE_DIR / name


def restore_skill(workspace: str, name: str) -> Path:
    """Devuelve una skill archivada a su sitio y la marca active."""
    skills_dir = Paths.workspace_skills_dir(workspace)
    archive_root = skills_dir / ARCHIVE_DIR
    src = archive_root / name
    if not src.is_dir():
        # versiones con stamp: restaura la más reciente
        stamped = sorted(archive_root.glob(f"{name}.*"))
        if not stamped:
            raise FileNotFoundError(f"'{name}' no está en el archivo")
        src = stamped[-1]
    dst = skills_dir / name
    if dst.exists():
        raise FileExistsError(f"'{name}' ya existe en skills/ — archívala primero")
    src.rename(dst)
    data = _load_store(workspace)
    rec = data["skills"].setdefault(name, {})
    rec["state"] = "active"
    rec["last_activity_at"] = _now().isoformat()
    _save_store(workspace, data)
    return dst
