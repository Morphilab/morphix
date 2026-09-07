# tests/test_bot_templates.py — template-first
"""Validación fail-loud de plantillas, sync YAML→DB y export de huérfanas."""

import secrets

import pytest
import yaml

from core.bot_templates import (
    BotTemplate,
    export_orphan_bots,
    list_bot_templates,
    load_bot_template,
    read_bot_template_fields,
    sync_bot_templates,
    write_bot_template,
)
from core.bots import BotsService
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
)
from core.path_resolver import paths

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)

VALID = {
    "display_name": "Test",
    "description": "bot de prueba",
    "soul_md": "Eres un bot de prueba.",
    "temperature": 0.5,
    "tool_names": ["web_fetch"],
    "skill_allowlist": [],
    "can_pause": True,
    "dm_enabled": True,
    "agent": "conversacional",
}


def test_template_rejects_unknown_field():
    with pytest.raises(Exception, match="extra"):
        BotTemplate.model_validate({**VALID, "slug": "x1", "campo_fantasma": 1})


def test_template_rejects_empty_soul():
    with pytest.raises(Exception, match="soul_md"):
        BotTemplate.model_validate({**VALID, "slug": "x1", "soul_md": "   "})


def test_template_rejects_bad_slug():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="slug"):
        BotTemplate.model_validate({**VALID, "slug": "Con Espacio"})
    with pytest.raises(ValidationError, match="slug"):
        BotTemplate.model_validate({**VALID, "slug": "-empieza-guion"})


def test_load_rejects_unknown_tools(monkeypatch, tmp_path):
    """Fail-loud: tool inexistente en el registry ⇒ ValueError accionable."""
    d = tmp_path / "bots"
    d.mkdir()
    (d / "malboto.yaml").write_text(yaml.safe_dump({**VALID, "slug": "malboto"}))
    monkeypatch.setattr(paths, "workspace_bots_dir", lambda ws: d)
    monkeypatch.setattr("core.bot_templates._known_tools", lambda: {"web_fetch"})
    with pytest.raises(ValueError, match="tools inexistentes.*fantasma"):
        write_bot_template("ws", "malboto", {**VALID, "tool_names": ["fantasma"]})


def test_filename_slug_is_authoritative(monkeypatch, tmp_path):
    """El stem del archivo gobierna el slug; las tools se validan contra el
    catálogo real (web_fetch existe ⇒ sin error)."""
    d = tmp_path / "bots"
    d.mkdir()
    (d / "otro.yaml").write_text(yaml.safe_dump(dict(VALID)))  # sin slug
    monkeypatch.setattr(paths, "workspace_bots_dir", lambda ws: d)
    t = load_bot_template("ws", "otro")
    assert t["can_pause"] is True  # del YAML
    fields = read_bot_template_fields("ws", "otro")
    assert fields is not None


def test_base_template_exists_and_is_valid():
    """El catálogo global trae 'base' — el sistema no arranca con roster vacío."""
    assert "base" in list_bot_templates(None)
    fields = read_bot_template_fields(None, "base")
    assert fields is not None
    assert fields["soul_md"].strip()
    assert fields["agent"] == "conversacional"


@_pg
@pytest.mark.asyncio
async def test_write_load_sync_roundtrip(monkeypatch):
    """write → sync crea la fila DB; update del YAML sincroniza identity fields."""
    sch = f"btpl_{secrets.token_hex(4)}"
    import shutil

    d = paths.workspace_bots_dir("main").parent / f"bots_test_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)

            monkeypatch.setattr(
                "core.workspaces.get_global_workspaces",
                lambda: type("W", (), {"current": "main"})(),
            )
            # aislamiento: redirige el dir de bots del workspace a uno temporal
            monkeypatch.setattr(paths, "workspace_bots_dir", lambda ws: d)

            write_bot_template("main", "nuevoboto", dict(VALID))
            res = await sync_bot_templates("main")
            assert "nuevoboto" in res["created"]

            row = await BotsService.get_bot("nuevoboto")
            assert row is not None
            assert row["soul_md"] == VALID["soul_md"]
            assert row["tool_names"] == ["web_fetch"]

            # update: cambia el SOUL en YAML → sync actualiza la fila
            write_bot_template("main", "nuevoboto", {**VALID, "soul_md": "SOUL v2"})
            res2 = await sync_bot_templates("main")
            assert "nuevoboto" in res2["updated"]
            assert (await BotsService.get_bot("nuevoboto"))["soul_md"] == "SOUL v2"

            # idempotente: tercer sync sin cambios
            res3 = await sync_bot_templates("main")
            assert "nuevoboto" in res3["unchanged"]

            # corrupto: no bloquea el sync del resto
            (d / "roto.yaml").write_text("slug: [objeto roto")
            res4 = await sync_bot_templates("main")
            assert "roto" in res4["errors"]
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)


@_pg
@pytest.mark.asyncio
async def test_export_orphan_bots_migrates_legacy_rows(monkeypatch):
    """Bots pre-plantilla (alfa/beta… creados por API libre) se exportan a YAML."""
    sch = f"bexp_{secrets.token_hex(4)}"
    import shutil

    d = paths.workspace_bots_dir("main").parent / f"bots_test_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            monkeypatch.setattr(
                "core.workspaces.get_global_workspaces",
                lambda: type("W", (), {"current": "main"})(),
            )
            monkeypatch.setattr(paths, "workspace_bots_dir", lambda ws: d)

            await BotsService.create_bot("legado1", soul_md="SOUL del legado")
            exported = await export_orphan_bots("main")
            assert "legado1" in exported
            fields = read_bot_template_fields("main", "legado1")
            assert fields["soul_md"] == "SOUL del legado"
            # conservador: huérfanas sin can_pause
            assert fields["can_pause"] is False

            # idempotente: segunda pasada no exporta nada
            assert await export_orphan_bots("main") == []
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)
