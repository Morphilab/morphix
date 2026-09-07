# tools/specs.py
"""
Tool Specifications — JSON Schema definitions para function-calling nativo.
Reemplaza el antiguo sistema text-based de build_tool_instructions().
"""

from dataclasses import dataclass, field
from typing import Any

from core.constants import PROJECTS_DIR_NAME
from core.path_resolver import paths


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    required: list[str] | None = None

    def to_openai_spec(self, strict: bool = False) -> dict:
        if self.required is None:
            req = [list(self.parameters.keys())[0]] if self.parameters else []
        else:
            req = self.required
        spec: dict = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": req,
                },
            },
        }
        if strict:
            # MCP tools have external schemas that may not comply with
            # DeepSeek strict mode requirements (nested objects, types, etc).
            # Skip strict mode for MCP-origin tools.
            is_mcp = self.name.startswith("mcp:") or self.name.startswith("mcp_")
            if not is_mcp:
                spec["function"]["strict"] = True
                spec["function"]["parameters"]["additionalProperties"] = False
                spec["function"]["parameters"]["required"] = list(self.parameters.keys())
        return spec


# ── Definiciones de herramientas ──────────────────────────────

TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "file_manager": ToolDefinition(
        name="file_manager",
        description="Lee y escribe archivos en el espacio de trabajo del proyecto.",
        parameters={
            "action": {
                "type": "string",
                "enum": ["write", "read", "append", "delete"],
                "description": "Operación a realizar: write (crear/sobrescribir), read (leer), append (añadir), delete (eliminar).",
            },
            "path": {
                "type": "string",
                "description": "Ruta relativa del archivo dentro del proyecto (ej: 'src/main.py', 'tests/test_app.py').",
            },
            "content": {
                "type": "string",
                "description": "Contenido a escribir (solo para acción 'write' o 'append').",
            },
        },
        required=["action", "path"],
    ),
    "git_manager": ToolDefinition(
        name="git_manager",
        description="Gestiona repositorios Git: inicializar, añadir archivos, hacer commits, consultar historial y contenido histórico (show).",
        parameters={
            "action": {
                "type": "string",
                "enum": ["init", "add", "commit", "log", "diff", "show"],
                "description": "Operación Git: init (inicializar repo), add (stage archivos), commit (guardar cambios), log (historial), diff (cambios), show (contenido de un archivo en una ref — read-only).",
            },
            "message": {
                "type": "string",
                "description": "Mensaje del commit (solo para acción 'commit').",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto donde ejecutar el comando Git (ej: '{PROJECTS_DIR_NAME}/miapp').",
            },
            "ref_path": {
                "type": "string",
                "description": "Referencia para action='show' (ej. 'HEAD:README.md', 'public:docs/x.md').",
            },
            "excludes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Pathspecs de exclusión para action='add' (solo add). Por defecto se excluyen automáticamente *.env, *.pem, *.key y secrets/**.",
            },
        },
        required=["action"],
    ),
    "code_exec": ToolDefinition(
        name="code_exec",
        description="Ejecuta código Python en un sandbox seguro (RestrictedPython). Soporta math, numpy, matplotlib. Bloquea acceso a sistema de archivos y red.",
        parameters={
            "code": {
                "type": "string",
                "description": "Código Python a ejecutar. Se ejecuta en sandbox con timeout de 10s.",
            },
        },
        required=["code"],
    ),
    "lsp_manager": ToolDefinition(
        name="lsp_manager",
        description="Analiza código Python con LSP (Jedi): definiciones, hover, diagnósticos, referencias.",
        parameters={
            "action": {
                "type": "string",
                "enum": ["definition", "hover", "diagnostics", "references", "ruff_check"],
                "description": "Tipo de análisis LSP: definition, hover, diagnostics (Jedi), references, ruff_check (linter real).",
            },
            "file": {
                "type": "string",
                "description": "Ruta del archivo a analizar (ej: 'src/main.py').",
            },
            "line": {
                "type": "integer",
                "description": "Número de línea (0-indexado) para definition/hover/references.",
            },
            "character": {
                "type": "integer",
                "description": "Número de carácter (0-indexado) para definition/hover/references.",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto (ej: '{PROJECTS_DIR_NAME}/miapp').",
            },
        },
        required=["action"],
    ),
    "pdf_read": ToolDefinition(
        name="pdf_read",
        description="Extrae texto de archivos PDF del proyecto.",
        parameters={
            "path": {
                "type": "string",
                "description": "Ruta del archivo PDF a leer, relativa al directorio del proyecto.",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto (ej: '{PROJECTS_DIR_NAME}/miapp').",
            },
        },
        required=["path"],
    ),
    "memory_inspector": ToolDefinition(
        name="memory_inspector",
        description=(
            "Inspecciona la memoria persistente del workspace activo: lista claves, "
            "lee valores o borra una clave (borrado requiere confirm_delete=true)."
        ),
        parameters={
            "action": {
                "type": "string",
                "enum": ["list", "read", "delete"],
                "description": "Operación a realizar.",
            },
            "key": {
                "type": "string",
                "description": "Clave de memoria (para read/delete).",
            },
            "confirm_delete": {
                "type": "boolean",
                "description": "Debe ser true para ejecutar delete.",
            },
        },
        required=["action"],
    ),
    "file_view": ToolDefinition(
        name="file_view",
        description=(
            "Abre un archivo del proyecto en una ventana de visualización para el "
            "usuario (md/pdf/html renderizados; otros como texto). NO devuelve el "
            "contenido al agente: para leerlo usa pdf_read o file_manager."
        ),
        parameters={
            "path": {
                "type": "string",
                "description": "Ruta del archivo, relativa al directorio del proyecto.",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto (ej: '{PROJECTS_DIR_NAME}/miapp').",
            },
        },
        required=["path"],
    ),
    "project_docs": ToolDefinition(
        name="project_docs",
        description=(
            "Project Knowledge Base (PKB): consulta el conocimiento curado del "
            "proyecto — convenciones, ADRs, checklists, contratos API y guías en "
            "workspaces/<ws>/knowledge/. SOLO LECTURA: list (categorías), read "
            "(documento), search (por palabras), inject (contenido top-k relevante "
            "para inyectar en tu contexto)."
        ),
        parameters={
            "action": {
                "type": "string",
                "enum": ["list", "read", "search", "inject"],
                "description": "Operación: list/read/search/inject.",
            },
            "category": {
                "type": "string",
                "description": "Categoría (conventions, decisions, checklists, api, onboarding, ...).",
            },
            "name": {
                "type": "string",
                "description": "Nombre del documento (para read).",
            },
            "query": {
                "type": "string",
                "description": "Texto de búsqueda (search/inject).",
            },
            "k": {
                "type": "integer",
                "description": "Máx. documentos a inyectar (default 3, máx 10).",
            },
        },
        required=["action"],
    ),
    "vision_analyze": ToolDefinition(
        name="vision_analyze",
        description=(
            "Analiza una imagen del proyecto con el rol de visión: describe su "
            "contenido, extrae texto (OCR) o interpreta gráficos. Retorna texto."
        ),
        parameters={
            "path": {
                "type": "string",
                "description": "Ruta de la imagen, relativa al directorio del proyecto (JPEG/PNG/GIF/WebP, máx 32 MiB).",
            },
            "mode": {
                "type": "string",
                "enum": ["describe", "ocr", "chart"],
                "description": "Tipo de análisis: describe (descripción general), ocr (texto), chart (gráficos).",
            },
            "prompt": {
                "type": "string",
                "description": "Instrucción libre que reemplaza el prompt del modo elegido.",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto (ej: '{PROJECTS_DIR_NAME}/miapp').",
            },
        },
        required=["path"],
    ),
    "test_runner": ToolDefinition(
        name="test_runner",
        description="Ejecuta tests con pytest y devuelve resultados (pasados, fallidos, errores).",
        parameters={
            "file_path": {
                "type": "string",
                "description": "Ruta del archivo de test a ejecutar (ej: 'tests/test_app.py').",
            },
            "test_name": {
                "type": "string",
                "description": "Nombre específico de test (opcional, formato: 'test_function' o 'TestClass::test_method').",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto (ej: '{PROJECTS_DIR_NAME}/miapp'). Obligatorio.",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace activo (ej: 'main'). Por defecto: 'main'.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout máximo en segundos (por defecto: 30).",
            },
        },
        required=["file_path", "project_root"],
    ),
    "diff_editor": ToolDefinition(
        name="diff_editor",
        description="Edita archivos aplicando diffs unificados (cambios quirúrgicos sin reescribir todo el archivo).",
        parameters={
            "file_path": {
                "type": "string",
                "description": "Ruta del archivo a editar.",
            },
            "action": {
                "type": "string",
                "enum": ["apply", "create"],
                "description": "'apply' para aplicar un diff, 'create' para generar un diff de cambios actuales.",
            },
            "diff_content": {
                "type": "string",
                "description": "Contenido del diff unificado a aplicar (solo para action='apply').",
            },
            "project_root": {
                "type": "string",
                "description": f"Directorio del proyecto (ej: '{PROJECTS_DIR_NAME}/miapp'). Obligatorio.",
            },
        },
        required=["action", "file_path"],
    ),
    "bash_manager": ToolDefinition(
        name="bash_manager",
        description="Ejecuta comandos shell de forma segura en el workspace del proyecto. "
        "IMPORTANTE: El parámetro 'command' es OBLIGATORIO. "
        "⚠️ The shell's working directory is ALREADY set to the project root "
        "(e.g. memory/main/{PROJECTS_DIR_NAME}/<name>). Run scripts directly by name — "
        "do NOT use 'cd code_projects/...' or any 'cd' command. "
        "NUNCA uses 'cd' — el shell ya está en el directorio correcto. "
        "Útil para: python3 script.py, pytest, npm install, git status, grep, find, etc.",
        parameters={
            "command": {
                "type": "string",
                "description": "Comando shell a ejecutar. OBLIGATORIO. Ej: 'pytest tests/', 'npm install', 'git status', 'python script.py'.",
            },
        },
        required=["command"],
    ),
    "web_search": ToolDefinition(
        name="web_search",
        description="Busca en la web usando Google Custom Search. Útil para encontrar información actualizada, documentación, o cualquier dato que el LLM no conozca.",
        parameters={
            "query": {
                "type": "string",
                "description": "Términos de búsqueda.",
            },
            "num": {
                "type": "integer",
                "description": "Número de resultados (máx 10, default 5).",
            },
        },
        required=["query"],
    ),
    "web_fetch": ToolDefinition(
        name="web_fetch",
        description="Obtiene el contenido de una URL y lo devuelve como texto plano. Útil para leer documentación, blog posts, o cualquier página web.",
        parameters={
            "url": {
                "type": "string",
                "description": "URL completa a obtener (debe empezar con http:// o https://).",
            },
        },
    ),
    "code_search": ToolDefinition(
        name="code_search",
        description="Busca patrones regex en archivos del proyecto. Equivalente a grep recursivo. Útil para encontrar definiciones, usos, o patrones específicos en el código.",
        parameters={
            "pattern": {
                "type": "string",
                "description": "Patrón regex a buscar (ej: 'def foo', 'import os', 'class.*Manager').",
            },
            "path": {
                "type": "string",
                "description": "Directorio relativo de búsqueda (defecto: '.' = raíz del proyecto).",
            },
            "include": {
                "type": "string",
                "description": "Glob de archivos a incluir (defecto: '*.py'). Usar '*.*' para todos.",
            },
            "max_results": {
                "type": "integer",
                "description": "Máximo de resultados (defecto: 20).",
            },
        },
        required=["pattern"],
    ),
    "load_skill": ToolDefinition(
        name="load_skill",
        description=(
            "Carga una skill procedimental por su name y devuelve instrucciones "
            "paso a paso. Úsala cuando la sección <SKILLS> indique que una skill "
            "aplica a la tarea, ANTES de responder o escribir código."
        ),
        parameters={
            "name": {
                "type": "string",
                "description": "Nombre de la skill tal como aparece en la lista <SKILLS>.",
            }
        },
        required=["name"],
    ),
    "goal_create": ToolDefinition(
        name="goal_create",
        description=(
            "Crea un objetivo de largo plazo para el workspace (máquina de estados "
            "active/paused/blocked/complete con revisión CAS). Autoridad inicial goal_round."
        ),
        parameters={
            "title": {"type": "string", "description": "Título del goal."},
            "description": {"type": "string", "description": "Descripción (opcional)."},
            "max_rounds": {
                "type": "integer",
                "description": "Presupuesto de rounds antes de bloquear (default 5).",
            },
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
        required=["title"],
    ),
    "goal_get": ToolDefinition(
        name="goal_get",
        description="Lista los goals del workspace o lee uno por goal_id (con su revisión CAS).",
        parameters={
            "goal_id": {"type": "string", "description": "ID del goal (vacío = listar todos)."},
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
    ),
    "goal_update": ToolDefinition(
        name="goal_update",
        description=(
            "Actualiza un goal con compare-and-swap (expected_revision obligatorio). "
            "Estados: active→paused/blocked/complete; blocked→active exige authority='human'. "
            "complete es terminal."
        ),
        parameters={
            "goal_id": {"type": "string", "description": "ID del goal."},
            "expected_revision": {
                "type": "integer",
                "description": "Revisión CAS actual del goal (léela con goal_get).",
            },
            "status": {
                "type": "string",
                "enum": ["active", "paused", "blocked", "complete"],
                "description": "Nuevo estado (transición validada).",
            },
            "title": {"type": "string", "description": "Nuevo título (opcional)."},
            "description": {"type": "string", "description": "Nueva descripción (opcional)."},
            "authority": {
                "type": "string",
                "enum": ["human", "goal_round"],
                "description": "Autoridad de la decisión (human para desbloquear/completar).",
            },
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
        required=["goal_id", "expected_revision"],
    ),
    "goal_round": ToolDefinition(
        name="goal_round",
        description=(
            "Registra un round de trabajo sobre el goal (incrementa rounds con CAS). "
            "Superar max_rounds bloquea el goal automáticamente."
        ),
        parameters={
            "goal_id": {"type": "string", "description": "ID del goal."},
            "expected_revision": {
                "type": "integer",
                "description": "Revisión CAS actual (léela con goal_get).",
            },
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
        required=["goal_id", "expected_revision"],
    ),
    "todo_write": ToolDefinition(
        name="todo_write",
        description=(
            "Reemplaza la lista completa de tareas (whole-list) con CAS. "
            ">1 item in_progress es rechazado salvo allow_parallel_in_progress=true."
        ),
        parameters={
            "items": {
                "type": "array",
                "description": "Items [{text, status}], status: pending|in_progress|done|blocked.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "blocked"],
                        },
                    },
                },
            },
            "expected_revision": {
                "type": "integer",
                "description": "Revisión CAS (vacío = primero).",
            },
            "allow_parallel_in_progress": {
                "type": "boolean",
                "description": "Permite >1 in_progress (default false).",
            },
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
        required=["items"],
    ),
    "todo_get": ToolDefinition(
        name="todo_get",
        description="Lee la lista de tareas del workspace (con su revisión CAS).",
        parameters={
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
    ),
    "plan_mode": ToolDefinition(
        name="plan_mode",
        description="Activa (o reemplaza) el plan activo del workspace con sus pasos.",
        parameters={
            "title": {"type": "string", "description": "Título del plan."},
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Pasos del plan.",
            },
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
        required=["title"],
    ),
    "exit_plan_mode": ToolDefinition(
        name="exit_plan_mode",
        description=(
            "Salida del plan-mode con decisión explícita: Approve (acepta y desactiva el plan) "
            "o Keep-planning (sigue planificando). Requiere la revisión CAS actual."
        ),
        parameters={
            "decision": {
                "type": "string",
                "enum": ["Approve", "Keep-planning"],
                "description": "Decisión de salida (default Keep-planning).",
            },
            "expected_revision": {
                "type": "integer",
                "description": "Revisión CAS del plan (léela con plan_mode).",
            },
            "workspace": {"type": "string", "description": "Workspace (default: activo)."},
        },
        required=["decision"],
    ),
}


def expand_allowed_tools(allowed_tools: list[str] | None) -> list[str] | None:
    """Expand tool group names into individual tool names.

    If an allowed_tools entry is NOT an exact match for any TOOL_DEFINITIONS key,
    treat it as a prefix and expand to all matching keys. This bridges the gap
    between agent profiles (e.g., tools: ["browser"]) and MCP-registered tool names
    (e.g., "mcp_browser_browser_navigate" sanitized for DeepSeek strict mode).

    Example: ["browser", "file_manager"] → ["mcp_browser_browser_navigate", ..., "file_manager"]
    """
    if allowed_tools is None:
        return None

    expanded: list[str] = []
    for entry in allowed_tools:
        if entry in TOOL_DEFINITIONS:
            expanded.append(entry)
            continue
        # Exact key prefix
        matched = [key for key in TOOL_DEFINITIONS if key.startswith(entry)]
        if not matched:
            # Original MCP prefix: "mcp:entry" in key or ".entry" in key
            matched = [
                key for key in TOOL_DEFINITIONS if f"mcp:{entry}" in key or f".{entry}" in key
            ]
        if not matched:
            # Sanitized MCP prefix: "mcp_entry" as prefix
            matched = [key for key in TOOL_DEFINITIONS if key.startswith(f"mcp_{entry}_")]
        if matched:
            expanded.extend(matched)
        else:
            expanded.append(entry)
    return expanded


def build_tool_definitions(
    allowed_tools: list[str] | None = None, provider_kind: str = "openai"
) -> list[dict]:
    """Devuelve las definiciones de herramientas en formato OpenAI function-calling.
    Si allowed_tools es None, devuelve todas. Si es una lista, filtra por nombres.

    provider_kind: "ollama" | "openai" — strict mode and all-required params
    only applied for OpenAI-compatible providers.
    """
    from core.config import settings

    strict = settings.deepseek_strict_mode and provider_kind != "ollama"
    expanded = expand_allowed_tools(allowed_tools)
    specs = []
    for name, tool_def in TOOL_DEFINITIONS.items():
        if expanded is None or name in expanded:
            specs.append(tool_def.to_openai_spec(strict=strict))
    return specs


# ── Legacy support: build_tool_instructions (text-based) ──────────────


def build_tool_instructions(
    allowed_tools: list[str] | None = None,
    project_root: str | None = None,
    plan_mode: bool = True,
    workspace: str | None = None,
) -> str:
    """Instrucciones textuales para el LLM (fallback cuando no hay function-calling nativo).
    Mantenido por compatibilidad con el modo Ollama y el sistema legacy."""
    if not allowed_tools:
        return "No hay herramientas disponibles para esta tarea."

    if workspace is None:
        from core.workspaces import get_global_workspaces

        workspace = get_global_workspaces().current

    lines = [
        "## 🛠️ Herramientas disponibles",
        "",
        "Debes responder con un JSON que contenga las acciones a ejecutar.",
    ]
    if plan_mode:
        lines.append('Formato: {"actions": [{"tool": "...", "action": "...", "params": {...}}]}')
    else:
        lines.append("Formato: objetos JSON concatenados: ")
        lines.append('{"tool": "...", "action": "...", "params": {...}}')

    lines.append("")

    for name in expand_allowed_tools(allowed_tools) or []:
        tool_def = TOOL_DEFINITIONS.get(name)
        if tool_def:
            lines.append(f"### {tool_def.name}")
            lines.append(f"{tool_def.description}")
            lines.append(f"Parámetros: {', '.join(tool_def.parameters.keys())}")
            lines.append("")

    if project_root:
        project_path = paths.code_projects_dir(workspace, project_root)
        lines.append(f"El directorio del proyecto es: {project_path}")
        lines.append("")

    lines.append("⚠️ Reglas obligatorias:")
    lines.append("- Usa 'file_manager' con action='write' para crear o modificar archivos.")
    lines.append("- Después de escribir archivos, usa 'git_manager' para init/add/commit.")
    lines.append("- Si hay test, escribe el archivo de test con pytest.")
    lines.append("- No uses herramientas que no estén en la lista de disponibles.")

    return "\n".join(lines)


def tool_matches_allowlist(tool_name: str, allowlist: list[str]) -> bool:
    """Check if a tool name matches the workflow allowlist.

    Supports exact match, prefix match (mcp:browser.* vs 'browser'),
    component match ('mcp:browser' in tool_name), and sanitized
    prefix match (mcp_browser_* for DeepSeek strict mode compat).
    """
    if tool_name in allowlist:
        return True
    for entry in allowlist:
        if tool_name.startswith(entry):
            return True
        if f"mcp:{entry}" in tool_name or f".{entry}" in tool_name:
            return True
        # Sanitized MCP names: mcp_browser_browser_navigate
        if tool_name.startswith(f"mcp_{entry}_"):
            return True
    return False
