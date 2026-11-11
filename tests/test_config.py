"""Guard tests para defaults de configuración sensibles.

El rol `fast` bajó a max_tokens=512 para contener el reasoning, pero la
evidencia de validación mostró starvation: "Respuesta vacía
por agotamiento de tokens (reasoning)". Mínimo aceptado: 1024.
"""

from core.config import Settings


def test_fast_role_max_tokens_at_least_1024():
    roles = Settings.model_fields["model_roles"].default_factory()
    assert roles["fast"]["max_tokens"] >= 1024


def test_fast_role_has_tool_calling():
    roles = Settings.model_fields["model_roles"].default_factory()
    assert roles["fast"].get("tool_calling") is True


def test_token_budget_single_canonical_value(monkeypatch):
    """El default de Settings y example.env deben coincidir (80000)."""
    import re
    from pathlib import Path

    from core.config import Settings

    monkeypatch.delenv("TOOL_MAX_TOKENS_PER_WORKFLOW", raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.tool_max_tokens_per_workflow == 80000

    root = Path(__file__).parent.parent
    example_env = (root / "example.env").read_text()
    m = re.search(r"^TOOL_MAX_TOKENS_PER_WORKFLOW=(\d+)$", example_env, re.M)
    assert m, "example.env no define TOOL_MAX_TOKENS_PER_WORKFLOW"
    assert int(m.group(1)) == 80000


def test_vision_role_exists_and_shape():
    """Rol vision dedicado: one-shot sin tools, con fallback local de vision."""
    roles = Settings.model_fields["model_roles"].default_factory()
    v = roles.get("vision")
    assert v, "falta rol 'vision' en model_roles"
    assert v["tool_calling"] is False
    assert v["provider"] == "deepinfra"
    assert v["model"] == "deepseek-ai/DeepSeek-V4.1-Flash-1017"
    assert "qwen2.5vl" in v["ollama_model"]


def test_todos_los_roles_de_producto_corren_en_deepinfra():
    """El proveedor de chat de producto es DeepInfra para TODOS los roles;
    los demas proveedores quedan soportados en la fabrica sin routing."""
    roles = Settings.model_fields["model_roles"].default_factory()
    assert set(roles) >= {
        "default",
        "fast",
        "reasoning",
        "agent",
        "creative",
        "critique",
        "vision",
        "deepinfra",
    }
    for nombre, rol in roles.items():
        assert rol["provider"] == "deepinfra", f"rol {nombre} fuera de DeepInfra"
        assert rol["model"] == "deepseek-ai/DeepSeek-V4.1-Flash-1017"
        assert rol["max_tokens"] >= 1024


def test_deepinfra_role_exists_and_shape():
    """Rol deepinfra aditivo: proveedor DeepInfra, thinking model.

    max_tokens con paridad con 'agent' — el reasoning_content comparte el
    cap y el starve-retry del loop lo duplica.
    """
    roles = Settings.model_fields["model_roles"].default_factory()
    d = roles.get("deepinfra")
    assert d, "falta rol 'deepinfra' en model_roles"
    assert d["provider"] == "deepinfra"
    assert d["model"] == "deepseek-ai/DeepSeek-V4.1-Flash-1017"
    assert d["max_tokens"] >= 1024
    assert d["tool_calling"] is True


def test_deepinfra_settings_defaults(monkeypatch):
    """Clave vacía por defecto + base OpenAI-compat de DeepInfra."""
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.delenv("DEEPINFRA_API_BASE", raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.deepinfra_api_key == ""
    assert fresh.deepinfra_api_base == "https://api.deepinfra.com/v1/openai"
