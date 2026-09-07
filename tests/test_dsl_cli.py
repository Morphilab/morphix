# tests/test_dsl_cli.py
"""Tests de la CLI morphix-workflow (new/validate/list).

Sin LLM ni DB: validate usa el compilador real; new genera esqueletos que
SIEMPRE validan (guard contra esqueletos rotos); paths de workspace
parcheados a tmp.
"""

import io

import pytest

from orchestration.dsl import cli


@pytest.fixture
def fake_ws_dir(tmp_path, monkeypatch):
    """Redirige workspaces/<ws>/workflows a tmp_path."""
    from core.path_resolver import PathResolver

    monkeypatch.setattr(
        PathResolver,
        "workspace_workflows_dir",
        staticmethod(lambda ws: tmp_path / ws / "workflows"),
    )
    return tmp_path


class TestValidate:
    def test_valida_documento_bueno(self, fake_ws_dir, tmp_path):
        p = tmp_path / "bueno.yaml"
        p.write_text(
            "version: 1\n"
            "name: bueno\n"
            "agents:\n"
            "  allowed: [developer]\n"
            "steps:\n"
            "  - id: paso1\n"
            "    kind: agent\n"
            "    agent: developer\n",
            encoding="utf-8",
        )
        out = io.StringIO()
        assert cli.cmd_validate(str(p), "main", out) == 0
        assert "✔" in out.getvalue()

    def test_valida_documento_sin_version(self, fake_ws_dir, tmp_path):
        p = tmp_path / "legacy.yaml"
        p.write_text("name: x\nsteps: []\n", encoding="utf-8")
        out = io.StringIO()
        assert cli.cmd_validate(str(p), "main", out) == 1
        assert "version" in out.getvalue()

    def test_valida_documento_malo_reporta_errores(self, fake_ws_dir, tmp_path):
        p = tmp_path / "malo.yaml"
        p.write_text(
            "version: 1\n" "steps:\n" "  - id: p\n" "    kind: agent\n" "    agent: intruso\n",
            encoding="utf-8",
        )
        out = io.StringIO()
        assert cli.cmd_validate(str(p), "main", out) == 1
        assert "intruso" in out.getvalue()

    def test_valida_preset_global_por_nombre(self, fake_ws_dir):
        out = io.StringIO()
        assert cli.cmd_validate("development", None, out) == 0

    def test_valida_preset_inexistente(self, fake_ws_dir):
        out = io.StringIO()
        assert cli.cmd_validate("no_existe_xyz", "main", out) == 1


class TestNew:
    def test_new_genera_esqueleto_valido(self, fake_ws_dir):
        out = io.StringIO()
        assert cli.cmd_new("feature_auth", "development", "main", out) == 0
        assert "feature_auth.yaml" in out.getvalue()

        # el archivo generado debe VALIDAR (guard anti-esqueleto-roto)
        path = fake_ws_dir / "main" / "workflows" / "feature_auth.yaml"
        out2 = io.StringIO()
        assert cli.cmd_validate(str(path), "main", out2) == 0

    def test_new_nombre_invalido(self, fake_ws_dir):
        out = io.StringIO()
        assert cli.cmd_new("1Mal-nombre", "development", "main", out) == 1
        assert "Nombre inválido" in out.getvalue()

    def test_new_no_sobreescribe(self, fake_ws_dir):
        out = io.StringIO()
        assert cli.cmd_new("dup", "development", "main", out) == 0
        out2 = io.StringIO()
        assert cli.cmd_new("dup", "development", "main", out2) == 1
        assert "ya existe" in out2.getvalue()

    def test_new_todos_los_tipos_validan(self, fake_ws_dir):
        for wf_type in ("development", "tdd", "collaborative", "coordinated"):
            out = io.StringIO()
            assert cli.cmd_new(f"w_{wf_type}", wf_type, "main", out) == 0
            path = fake_ws_dir / "main" / "workflows" / f"w_{wf_type}.yaml"
            out2 = io.StringIO()
            assert cli.cmd_validate(str(path), "main", out2) == 0, wf_type


class TestList:
    def test_list_imprime_nombres(self, fake_ws_dir):
        out = io.StringIO()
        assert cli.cmd_list("main", out) == 0
        # fallback a templates globales (existen: development, tdd, ...)
        assert "development" in out.getvalue()


def test_skeleton_tdd_until_declara_args():
    """El skeleton que servimos a usuarios NO puede usar
    until sin args — test_runner requiere file_path y sin él el loop SIEMPRE
    agota max_iter (repite tareas, agota presupuesto)."""
    from orchestration.dsl.cli import _SKELETONS

    until = _SKELETONS["tdd"]["steps"][0]["until"]
    assert until["args"] == {"file_path": "."}
