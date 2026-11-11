# tests/test_expand_env_path.py
"""Project.root portable — la expansión ${VAR:-default}/~ vive en
`expand_dsl_project_root`."""

from orchestration.loader import expand_dsl_project_root


def test_expande_var_y_default(monkeypatch):
    monkeypatch.setenv("MI_DIR", "/tmp/morphix-xyz")
    out = expand_dsl_project_root("${MI_DIR}/sub")
    assert out == "/tmp/morphix-xyz/sub"
    out2 = expand_dsl_project_root("${NO_EXISTE_XYZ:-fallback}/sub")
    assert out2 == "fallback/sub"


def test_expande_tilde():
    out = expand_dsl_project_root("~/proyectos/demo")
    assert out is not None
    assert "~" not in out
    assert out.startswith("/")


def test_root_sin_expansion_queda_intacto():
    assert expand_dsl_project_root("code_projects/demo") == "code_projects/demo"
    assert expand_dsl_project_root(None) is None
