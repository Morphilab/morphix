"""Higiene de skills y kits.

- Toda key usada en `examples` existe como param en TOOL_DEFINITIONS.
- Golden del pipeline de skills (detecta drift entre skills y specs).
- tools/kits no es paquete Python (solo datos YAML).
"""

import re
from pathlib import Path

import yaml

from tools.specs import TOOL_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "tools" / "skills"
KITS_DIR = REPO_ROOT / "tools" / "kits"

_KEY_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=")


def _example_keys(example: str) -> list[str]:
    """Extrae claves key=val de un ejemplo 'tool: accion, k1=v1, k2=v2'."""
    if ":" not in example:
        return []
    after = example.split(":", 1)[1]
    keys = []
    for token in after.split(","):
        match = _KEY_RE.search(token)
        if match:
            keys.append(match.group(1))
    return keys


def test_skill_examples_use_real_params():
    """Ninguna skill enseña claves que la tool no acepta."""
    errors: list[str] = []
    for skill_file in sorted(SKILLS_DIR.glob("*.yaml")):
        skill = yaml.safe_load(skill_file.read_text(encoding="utf-8"))
        tool = skill.get("tool")
        spec = TOOL_DEFINITIONS.get(tool)
        assert spec is not None, f"{skill_file.name}: tool '{tool}' sin ToolDefinition"
        valid = set(spec.parameters.keys())
        for example in skill.get("examples", []):
            for key in _example_keys(example):
                if key not in valid:
                    errors.append(f"{skill_file.name}: clave inexistente '{key}' en: {example}")
    assert not errors, "\n".join(errors)


def test_skill_pipeline_golden_render():
    """El pipeline renderiza las skills reales con las claves corregidas."""
    from orchestration.loop import _load_tool_skills

    out = _load_tool_skills(["lsp_manager", "code_search", "test_runner"])
    assert "LSP_MANAGER SKILL" in out
    assert "CODE_SEARCH SKILL" in out
    assert "TEST_RUNNER SKILL" in out
    # claves correctas visibles en los ejemplos renderizados
    assert "file=" in out
    assert "pattern=" in out
    assert "file_path=" in out


def test_kits_dir_is_data_only():
    """tools/kits contiene solo YAMLs — sin __init__.py fantasma."""
    assert KITS_DIR.is_dir()
    assert not (
        KITS_DIR / "__init__.py"
    ).exists(), "tools/kits/__init__.py sobra: los kits son datos YAML, no paquete"


def test_kit_yaml_params_reference_real_param_names():
    """Los params de los kits referencian nombres de params reales
    (higiene aunque los kits no los rendericen)."""
    for kit_file in sorted(KITS_DIR.glob("*.yaml")):
        kit = yaml.safe_load(kit_file.read_text(encoding="utf-8"))
        for i, step in enumerate(kit.get("steps", []), 1):
            tool = step.get("tool")
            spec = TOOL_DEFINITIONS.get(tool)
            if spec is None:
                continue
            params = step.get("params") or {}
            unknown = set(params.keys()) - set(spec.parameters.keys())
            assert (
                not unknown
            ), f"{kit_file.name} paso {i}: params {unknown} no existen en spec de '{tool}'"
