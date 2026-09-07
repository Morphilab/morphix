"""Ciclo de vida de aprobación de skills workspace.

Sidecar: workspaces/<ws>/skills/.aprobaciones.json — {name: {sha256, date}} o
{name: {"rejected": true}}. El contenido del skill manda: si cambia (hash
distinto), la aprobación CADUCA (pendiente de re-aprobación).
"""

import hashlib

import pytest

from core.skills import build_bootstrap  # noqa: E402
from core.skills_approval import (  # noqa: E402
    approval_status,
    approve_skill,
    load_approvals,
    reject_skill,
)


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """Workspace skills fake: skills dir con 2 skills locales."""
    from core import path_resolver

    ws_skills = tmp_path / "skills"
    for name, body in (
        ("propia_ok", "---\nname: propia_ok\ndescription: d1\n---\ncuerpo uno"),
        ("propia_mala", "---\nname: propia_mala\ndescription: d2\n---\ncuerpo dos"),
    ):
        d = ws_skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
    # core/skills.py usa la CLASE PathResolver; skills_approval la instancia.
    # Parchear la clase cubre ambas referencias.
    monkeypatch.setattr(
        path_resolver.PathResolver,
        "workspace_skills_dir",
        staticmethod(lambda w: ws_skills),
    )
    return tmp_path, ws_skills


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_pending_sin_aprobacion(ws):
    approval_status("w1")  # smoke import
    _, ws_skills = ws
    st = approval_status("w1")
    assert st == {"propia_ok": "pending", "propia_mala": "pending"}
    assert ws_skills.is_dir()


def test_approve_y_status_approved(ws):
    _, ws_skills = ws
    from core.skills import load_skill

    body = load_skill("propia_ok", "w1").body  # body parseado (canónico)
    approve_skill("w1", "propia_ok", body)
    assert approval_status("w1")["propia_ok"] == "approved"
    saved = load_approvals("w1")
    assert saved["propia_ok"]["sha256"] == _sha(body)


def test_cambio_de_contenido_caduca(ws):
    _, ws_skills = ws
    from core.skills import load_skill

    body = load_skill("propia_ok", "w1").body
    approve_skill("w1", "propia_ok", body)
    # cambio con frontmatter VÁLIDO (si perdiera el frontmatter dejaría de
    # ser skill y saldría del descubrimiento, no sería "changed")
    (ws_skills / "propia_ok" / "SKILL.md").write_text(
        "---\nname: propia_ok\ndescription: d1\n---\n" + body + "\nimport os  # cambio sospechoso",
        encoding="utf-8",
    )
    assert approval_status("w1")["propia_ok"] == "changed"  # re-aprobar


def test_reject(ws):
    _, _ = ws
    reject_skill("w1", "propia_mala")
    assert approval_status("w1")["propia_mala"] == "rejected"
    # reject sobrevive en el sidecar
    assert load_approvals("w1")["propia_mala"].get("rejected") is True


def test_copia_identica_a_global_es_aprobada_implicitamente(tmp_path, monkeypatch):
    """_bootstrap_workspace_skills COPIA las skills globales a
    workspaces/<ws>/skills/ (aditivo); esas copias son source="workspace".
    Contrato: copia byte-identica a la global del mismo nombre = aprobada
    implícita (cero riesgo añadido); una versión que DIFIERE sigue
    requiriendo aprobación — sin aprobación implícita los agentes pierden
    TODAS sus skills en workspaces existentes."""
    from core import path_resolver
    from core.skills import build_bootstrap

    glob = tmp_path / "glob_skills"
    ws_skills = tmp_path / "skills"
    for name, body in (
        ("copiada", "---\nname: copiada\ndescription: d\n---\ncuerpo"),
        ("modificada", "---\nname: modificada\ndescription: d\n---\ncuerpo original"),
    ):
        for base in (glob, ws_skills):
            d = base / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(body, encoding="utf-8")
    # la copia local de 'modificada' diverge de la global
    (ws_skills / "modificada" / "SKILL.md").write_text(
        "---\nname: modificada\ndescription: d\n---\ncuerpo ALTERADO", encoding="utf-8"
    )
    monkeypatch.setattr(
        path_resolver.PathResolver, "templates_skills_dir", staticmethod(lambda: glob)
    )
    monkeypatch.setattr(
        path_resolver.PathResolver,
        "workspace_skills_dir",
        staticmethod(lambda w: ws_skills),
    )
    st = approval_status("w1")
    assert st["copiada"] == "approved"  # idéntica → implícita
    assert st["modificada"] == "pending"  # divergió → requiere aprobación
    out = build_bootstrap("w1")
    assert "- **copiada** (workspace)" in out
    assert "- **modificada**" not in out


def test_reject_explicito_gana_sobre_copia_identica(tmp_path, monkeypatch):
    """Si el usuario rechazó explícitamente, el rechazo prevalece incluso
    siendo copia idéntica a la global (decisión deliberada)."""
    from core import path_resolver

    glob = tmp_path / "glob_skills"
    ws_skills = tmp_path / "skills"
    for base in (glob, ws_skills):
        d = base / "copiada"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: copiada\ndescription: d\n---\ncuerpo", encoding="utf-8"
        )
    monkeypatch.setattr(
        path_resolver.PathResolver, "templates_skills_dir", staticmethod(lambda: glob)
    )
    monkeypatch.setattr(
        path_resolver.PathResolver,
        "workspace_skills_dir",
        staticmethod(lambda w: ws_skills),
    )
    reject_skill("w1", "copiada")
    assert approval_status("w1")["copiada"] == "rejected"


def test_bootstrap_oculta_no_aprobadas(ws):
    out = build_bootstrap("w1")
    assert "- **propia_ok**" not in out  # pending → NO listada como disponible
    assert "- **propia_mala**" not in out
    assert "NO aprobadas" in out  # nota explícita para el modelo
    assert "pendientes de aprobación: propia_mala, propia_ok" in out


def test_bootstrap_muestra_aprobadas_y_menciona_rechazada(ws):
    from core.skills import load_skill

    approve_skill("w1", "propia_ok", load_skill("propia_ok", "w1").body)
    reject_skill("w1", "propia_mala")
    out = build_bootstrap("w1")
    assert "- **propia_ok** (workspace)" in out  # aprobada → listada
    assert "- **propia_mala**" not in out  # rechazada → NO listada
    assert "rechazada" in out and "propia_mala" in out  # placeholder explícito


def test_bootstrap_global_no_afectado(ws, monkeypatch):
    """Sin skills workspace, el bootstrap queda sin notas adicionales."""
    from core import path_resolver

    empty = ws[1].parent / "vacio_skills"
    empty.mkdir()
    monkeypatch.setattr(
        path_resolver.PathResolver,
        "workspace_skills_dir",
        staticmethod(lambda w: empty),
    )
    out = build_bootstrap("w1")
    assert "NO aprobadas" not in out


@pytest.mark.asyncio
async def test_load_skill_tool_bloquea_no_aprobada(ws, monkeypatch):
    """Defensa en profundidad: el tool load_skill rechaza skills no aprobadas."""
    from types import SimpleNamespace

    from tools import skill_loader

    _, ws_skills = ws
    monkeypatch.setattr(
        "core.workspaces.get_global_workspaces",
        lambda: SimpleNamespace(current="w1"),
    )
    out = await skill_loader._load_skill_tool(name="propia_ok")
    assert "no está aprobada" in out
    from core.skills import load_skill

    approve_skill("w1", "propia_ok", load_skill("propia_ok", "w1").body)
    out2 = await skill_loader._load_skill_tool(name="propia_ok")
    assert "cuerpo uno" in out2


def test_dialog_gui_aprueba_y_consume(ws):
    """Smoke Qt: el diálogo lista pendientes, aprobar consume el item y
    escribe el sidecar en los paths parcheados del fixture."""
    from typing import cast

    from PySide6.QtWidgets import QApplication, QDialog

    from core.skills_approval import approval_status
    from desktop.widgets.skills_approval_dialog import SkillsApprovalDialog

    _qapp = cast(QApplication, QApplication.instance() or QApplication([]))
    assert _qapp is not None
    dlg = SkillsApprovalDialog("w1", [("propia_ok", "pending", "cuerpo uno")])
    assert dlg.skills_list.count() == 1
    assert "cuerpo uno" in dlg.preview.toPlainText()
    dlg._approve_current()  # consume → lista vacía → accept()
    assert dlg.result() == int(QDialog.DialogCode.Accepted)
    assert dlg.skills_list.count() == 0
    assert approval_status("w1")["propia_ok"] == "approved"
