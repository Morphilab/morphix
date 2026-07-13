# Adding Tools

This guide walks you through creating a new tool for Morphix. We'll use a concrete example: a `hello_world` tool that greets the user in different languages.

## Step 1: Create the tool module

Create `tools/hello_world.py`:

```python
"""
Hello World tool — saluda al usuario en varios idiomas.
"""
import logging

logger = logging.getLogger(__name__)


class HelloWorld:
    """Tool implementation class with static async execute method."""

    SUPPORTED_LANGUAGES = {
        "en": "Hello, {}!",
        "es": "¡Hola, {}!",
        "fr": "Bonjour, {}!",
        "de": "Hallo, {}!",
        "it": "Ciao, {}!",
        "pt": "Olá, {}!",
        "ja": "こんにちは、{}！",
    }

    @staticmethod
    async def execute(
        name: str = "World",
        language: str = "en",
        **kwargs,
    ) -> str:
        # Validate language
        if language not in HelloWorld.SUPPORTED_LANGUAGES:
            supported = ", ".join(HelloWorld.SUPPORTED_LANGUAGES.keys())
            return (
                f"❌ Unsupported language: '{language}'. "
                f"Supported: {supported}"
            )

        template = HelloWorld.SUPPORTED_LANGUAGES[language]
        greeting = template.format(name)
        logger.info(f"HelloWorld: greeting='{greeting}' lang='{language}'")
        return f"🎉 {greeting}"


# ── Registration ──
from tools.registry import tools_registry


@tools_registry.register("hello_world")
async def hello_world_tool(name: str = "World", language: str = "en", **kwargs) -> str:
    """Wrapper that the orchestrator calls. Infers defaults, validates, delegates."""
    return await HelloWorld.execute(name=name, language=language)
```

Key patterns:

- The class (`HelloWorld`) holds the business logic with a static `async execute()` method.
- The module-level function (`hello_world_tool`) is decorated with `@tools_registry.register("hello_world")`.
- Accept `**kwargs` for forward-compatibility — the orchestrator may pass extra params like `workspace`.
- Return strings (success or error). Use `❌` prefix for user-facing errors.

## Step 2: Add the function-calling spec

Open `tools/specs.py` and add a `ToolDefinition` entry to `TOOL_DEFINITIONS`:

```python
TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    # ... existing tools ...
    "hello_world": ToolDefinition(
        name="hello_world",
        description="Saluda al usuario en diferentes idiomas. Útil para demostraciones y tests.",
        parameters={
            "name": {
                "type": "string",
                "description": "Nombre de la persona a saludar (defecto: 'World').",
            },
            "language": {
                "type": "string",
                "enum": ["en", "es", "fr", "de", "it", "pt", "ja"],
                "description": "Código de idioma para el saludo (defecto: 'en').",
            },
        },
        required=["name"],
    ),
}
```

The `ToolDefinition` fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Must match the registry key used in `@tools_registry.register()` |
| `description` | `str` | Shown to the LLM; guides when to call the tool |
| `parameters` | `dict` | JSON Schema properties object |
| `required` | `list[str]` | List of required parameter names |

The `to_openai_spec()` method converts this to an OpenAI function-calling spec. The spec is automatically sent to the LLM so it knows how to call your tool.

!!! note "Tool name vs filename"
    The registered name does not need to match the filename. For example, `code_execution.py` registers as `code_exec`, and `pdf_reader.py` registers as `pdf_read`. Pick a short, descriptive name.

## Step 3: Load the tool at startup

Global tools in `tools/*.py` are loaded automatically at startup by `load_global_tools()`. No extra wiring needed.

For workspace-scoped tools, place them in `workspaces/<name>/tools/*.py`. They are loaded/cleared on workspace switch.

## Step 4: Write tests

Create `tests/test_hello_world.py`:

```python
import pytest

from tools.registry import ToolsRegistry


@pytest.mark.asyncio
async def test_hello_world_default():
    """Saludo por defecto (World, inglés)."""
    reg = ToolsRegistry()
    from tools.hello_world import hello_world_tool
    reg.register("hello_world")(hello_world_tool)

    tool = reg.get_tool("hello_world")
    result = await tool()
    assert "Hello, World!" in result


@pytest.mark.asyncio
async def test_hello_world_spanish():
    """Saludo en español con nombre personalizado."""
    reg = ToolsRegistry()
    from tools.hello_world import hello_world_tool
    reg.register("hello_world")(hello_world_tool)

    tool = reg.get_tool("hello_world")
    result = await tool(name="María", language="es")
    assert "¡Hola, María!" in result


@pytest.mark.asyncio
async def test_hello_world_invalid_language():
    """Idioma no soportado devuelve error."""
    reg = ToolsRegistry()
    from tools.hello_world import hello_world_tool
    reg.register("hello_world")(hello_world_tool)

    tool = reg.get_tool("hello_world")
    result = await tool(language="zz")
    assert result.startswith("❌")


def test_tool_registry_isolation():
    """Cada ToolsRegistry es independiente."""
    reg1 = ToolsRegistry()
    reg2 = ToolsRegistry()

    @reg1.register("tool_a")
    async def tool_a():
        return "a"

    assert reg1.get_tool("tool_a") is not None
    assert reg2.get_tool("tool_a") is None
```

Key testing patterns:

- Use `ToolsRegistry()` (not the global `tools_registry`) for test isolation.
- Mark async tests with `@pytest.mark.asyncio`.
- Register the tool in the test, then call it directly via `reg.get_tool(name)`.
- No shared fixtures in `conftest.py` — everything is inline.

## Step 5: Run the tests

```bash
poetry run pytest tests/test_hello_world.py -v
```

Expected output:

```
tests/test_hello_world.py::test_hello_world_default PASSED
tests/test_hello_world.py::test_hello_world_spanish PASSED
tests/test_hello_world.py::test_hello_world_invalid_language PASSED
tests/test_hello_world.py::test_tool_registry_isolation PASSED
```

## Step 6: Verify in the GUI

1. Add `"hello_world"` to the `tools.allowed` list in your active workflow template (e.g., `templates/workflows/development.yaml`).
2. Launch the GUI: `poetry run python run.py`
3. Open the Maestro tab and type: `hello_world: name=Alice, language=fr`
4. You should see the direct-tool response: `🎉 Bonjour, Alice!`

## Tool Implementation Checklist

- [ ] Class with `async execute()` method
- [ ] Module-level function decorated with `@tools_registry.register("name")`
- [ ] `**kwargs` in the wrapper function signature
- [ ] `ToolDefinition` entry in `tools/specs.py` `TOOL_DEFINITIONS`
- [ ] Error responses start with `❌` for user visibility
- [ ] Tests use `ToolsRegistry()` for isolation
- [ ] Tests cover: success path, error path, edge cases
- [ ] Added to workflow `tools.allowed` list
