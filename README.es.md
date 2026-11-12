[English](README.md) | **Español**

# Morphix — Plataforma de orquestación multi-agente

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![mypy](https://img.shields.io/badge/mypy-0%20errors-success)](https://github.com/morphilab/morphix)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Morphix** es una plataforma de orquestación multi-agente, con licencia MIT y arquitectura
por capas, para ingeniería de software asistida por IA. Coordina varios agentes de IA mediante
un motor determinista de workflows DSL (“el modelo elige, el motor acota”), con aislamiento
por workspace en PostgreSQL y patrones de infraestructura de producción.

> **Documentación:** la documentación completa está disponible en el
> [sitio de Morphix](https://morphilab.github.io/morphix). Para verla en local:
> `uv run mkdocs serve`.

---

## Estado del proyecto

| Métrica | Valor |
|---------|-------|
| Python | **3.12.15** fijado con uv (`.python-version`, intérprete gestionado por uv — desacoplado del Python del sistema), `>=3.12,<3.14` |
| Tests | **2.719** funciones de test en **295** módulos (medido el 2026-11-11 — recalcular con: `git grep -hE "def test_" -- 'tests/*.py' \| wc -l` / `ls tests/test_*.py \| wc -l`) |
| Versión | **1.2.0** (`pyproject.toml`) |
| Tipado | mypy sobre `core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/` &mdash; 0 errores |
| LLM por defecto | DeepInfra `deepseek-ai/DeepSeek-V4.1-Flash-1017` (todos los roles; requiere `DEEPINFRA_API_KEY`), fallback &rarr; Ollama |
| Tools | **24** registradas (23 con spec + `ask_clarification`, solo intercepción) |
| Agentes | **5** perfiles |
| Workflows | **10** presets DSL |

---

## ¿Por qué Morphix?

La mayoría de las herramientas de programación con IA son monolíticos fuertemente acoplados.
Morphix está diseñado como una **plataforma**: una arquitectura por capas que separa
responsabilidades para que cada subsistema se pueda entender, probar y extender de forma
independiente.

- **Arquitectura por capas** &mdash; `core/` &rarr; `llm/` &rarr; `agents/` &rarr; `tools/` &rarr; `orchestration/` &rarr; `desktop/`, más un harness independiente en `research/`. Los límites se aplican por convención; hay imports ascendentes documentados (MCP, bootstrap, loaders de workspace y otros) para preservar el orden de arranque, y no hay ciclos de import conocidos. La UI no se filtra a la lógica de negocio.
- **Schemas de PostgreSQL por workspace** &mdash; cada workspace vive en su propio schema con tablas aisladas. No hay fuga de datos entre proyectos. El schema activo se fija con `search_path` en cada sesión async.
- **Motor determinista de workflows DSL** &mdash; los workflows son documentos YAML (`version: 1`) que se compilan y validan antes de ejecutarse. El control de flujo (gates, loops, reintentos, fan-out paralelo, checkpoints humanos) vive en el motor, nunca en el LLM; las salidas del modelo se validan contra enums y fallbacks declarados. **10 presets de producto** listos para ejecutar.
- **Patrones de producción** &mdash; circuit breaker con fallback, rate limiter de ventana deslizante, sandbox de RestrictedPython en un subproceso confinado, memoria vectorial FAISS con self-healing, presupuesto de tokens y watermarking anti-distillation.
- **2.719 funciones de test (medido el 2026-11-11), 0 errores de mypy** &mdash; tests async con `pytest-asyncio`. Pipeline de CI completo con PostgreSQL como contenedor de servicios.
- **Extensibilidad dinámica** &mdash; tools como archivos `.py` cargados en runtime, agentes como perfiles YAML, hooks con 6 puntos de intercepción e integración MCP para servidores de tools externos.

> ⚠️ **Divulgación de IA / AI Disclosure**
>
> **Español:** Este proyecto fue desarrollado con asistencia de herramientas de
> inteligencia artificial. Dada la naturaleza automatizada de algunos componentes,
> se recomienda que los usuarios revisen y prueben el código independientemente antes
> de integrarlo en sus propios sistemas.
>
> **English:** This project was developed with assistance from artificial intelligence
> tools. Given the automated nature of some components, users are advised to review
> and test the code independently before integrating it into their own systems.

> **Nota sobre cobertura:** un ratchet de cobertura por archivo
> (`tests/test_coverage_ratchet.py` + `tests/_coverage_baseline.json`) compara
> `coverage.json` contra una baseline. Falla si un archivo medido baja más de
> **1 punto porcentual** o si baja el total; se salta cuando `coverage.json` falta o
> está viejo. El contrato es el ratchet, no la cifra agregada.

---

## Capturas

> Capturadas desde un workspace de demostración con conversaciones y workflows de ejemplo.

![Dashboard de Morphix — workflows, agentes, proyectos y bots](docs/assets/screenshots/dashboard.png)

*Dashboard — presets de workflow, perfiles de agentes, proyectos y bots en un solo lugar.*

![Maestro de Morphix — debate multi-agente con bloques por agente y actividad en vivo](docs/assets/screenshots/maestro-debate.png)

*Maestro — debate multi-agente con bloques por agente, consenso determinista y telemetría de actividad y tokens en vivo.*

---

## Inicio rápido

```bash
# Requisitos: PostgreSQL, uv (https://docs.astral.sh/uv/)
uv python install 3.12.15    # una vez: intérprete gestionado por uv, desacoplado del sistema
uv sync --all-groups         # crea .venv desde uv.lock (grupos dev + docs)
cp example.env .env          # edita DATABASE_URL + al menos una API key (DEEPINFRA_API_KEY — proveedor por defecto; también se soportan DeepSeek y otros)
uv run alembic upgrade head
uv run python run.py     # abre la GUI de escritorio
```

> Las migraciones de Alembic son **manuales**. `startup_db()` solo crea tablas directamente;
> ejecuta `uv run alembic upgrade head` para cambios basados en migraciones.

---

## Arquitectura

```
GUI de escritorio (PySide6)
        │
        ▼
WorkflowOrchestrator.run_full_workflow()   ── routing ──
   1. Comando directo de tool   "tool_name: action, key=val"
                                (allowlist leída del documento crudo — DSL-aware)
   2. Chat canónico de bot      → bots_runner (identidad Bot Mode, sin plantillas de workflow)
   3. Workflow DSL              doc con `version: 1` → compiler → validator → engine
                                (el YAML legacy sin `version: 1` recibe un error
                                 accionable que apunta al CLI del DSL)
        │
        ▼
   Agent Loop (ReAct)  ←→  ToolOrchestrator ──→ Tools (file/git/bash/code/search/MCP)
        │                          │
   Proveedor LLM               Hooks (6 puntos) · UI de permisos ·
   (DeepInfra/DeepSeek/         Presupuesto de tokens · Anti-distillation
   OpenAI/Grok/NVIDIA/Ollama)
```

### Mapa de capas

Morphix sigue una arquitectura por capas con límites claros; hay imports ascendentes
documentados (MCP, bootstrap, loaders de workspace y otros) para preservar el orden de
arranque:

| Capa | Directorio | Responsabilidad |
|------|-----------|-----------------|
| **Core** | `core/` | Lógica de negocio &mdash; base de datos, configuración, memoria (FAISS), dominio Bot Mode, seguridad, MCP, resolución de paths |
| **LLM** | `llm/` | Abstracción de IA &mdash; selección de modelo por rol, DeepInfra (por defecto), DeepSeek, OpenAI, Grok, pool NVIDIA, Ollama |
| **Agents** | `agents/` | Sistema de agentes &mdash; registry, loader, perfiles (5 agentes), ejecución, auditoría |
| **Tools** | `tools/` | Sistema de tools &mdash; specs, registry, orquestador con hooks, 23 specs + `ask_clarification` |
| **Orchestration** | `orchestration/` | Orquestación de workflows &mdash; **motor DSL** (`dsl/`), agent loop, decomposer, aggregator, finalizer, runtime de Bot Mode |
| **Desktop** | `desktop/` | GUI PySide6 &mdash; dashboard, Maestro multi-sesión, editor, analytics, historial, bots, memoria |
| **Research** | `research/` | Harness de evaluación independiente &mdash; batch runner + compresor de trayectorias; no está cableado al producto |

`viewer/` es un visor de archivos independiente (cero imports de Morphix) usado por la GUI y
por la tool `file_view`.

### Los workspaces son schemas de PostgreSQL

Cada workspace es un **schema de PostgreSQL separado** con sus propias tablas
(conversaciones, mensajes, workflows, usuarios, sesiones pausadas, bots, rutinas, salas de
grupo, turnos pendientes, etc.). Los nombres de schema cumplen `[a-z][a-z0-9_]*`. El schema
activo se fija con `search_path` en cada sesión async. Cambiar de workspace ejecuta
`create_schema`, `create_tables_in_schema` y `set_async_schema`, y después recarga agentes,
tools y hooks.

> `core/database.py` reescribe `postgresql://` &rarr; `postgresql+asyncpg://` para el motor
> async.

### Carga dinámica

- **Tools globales**: `tools/*.py`, cargadas al arrancar con `load_global_tools()`.
- **Tools de workspace**: `workspaces/<name>/tools/*.py`, cargadas/limpiadas al cambiar de workspace.
- **Agentes**: plantillas en `templates/agents/*.yaml`, copiadas a `workspaces/<name>/agents/`
  en el primer cambio, registradas en el `agents_registry` global.
- **Workflows**: documentos DSL en `templates/workflows/*.yaml`; el orden de búsqueda es
  copia del workspace &rarr; copia de la plantilla del workspace
  (`templates/workspaces/<ws>/workflows/`) &rarr; plantilla global del producto, resuelto por
  `orchestration/loader.py`.

---

## Workflows (motor DSL)

Los workflows son **documentos YAML con `version: 1`**, compilados (`dsl/compiler.py`),
validados (`dsl/validator.py`) y ejecutados por el motor determinista (`dsl/engine.py`). El
LLM nunca controla el flujo: las decisiones pasan por enums validados con fallbacks
declarados, y los loops/ramas están acotados por el motor (`max_iter`, reintentos, gates).

Funciones del motor **usadas por los presets incluidos**: reintentos acotados (`retry_max`),
gates condicionales (`when: if_blockers`), `loop` sobre ítems (secuencial y `parallel`),
`until` con condición validada de agente/tool/métrica, verificación determinista por paso
(`ToolStep.verdict`), rutas de test por run (`slug_path`, rutas por ciclo), verificación del
agregado acotada a lo que escribió el run (`files_del_run`) y una suite de conformance
ejecutable (`dsl/conformance.py`) que corre cada preset contra el motor real.

El DSL también ofrece una **superficie de extensibilidad para el usuario**: ramas `decide`,
ramas `parallel` estáticas, includes anidados de workflows, **fragmentos de body**
(`kind: fragment` — cuerpos de pasos compartidos, expandidos en compile-time desde archivos
del catálogo con prefijo `_`), `checkpoint`s humanos, `commit_after`, `decompose: dag` y
`on_error`. Los fragmentos de body los usan `development`/`coordinated`; `checkpoint` y
`query_contains` los usan los presets de workspaces aislados; el resto de mecanismos queda
disponible para tus propios workflows. Un test estructural
(`test_capacidades_sin_uso_en_presets_son_las_conocidas`) mantiene ese inventario honesto.

**10 presets de producto** en `templates/workflows/`, **curados en 5 core** (el dashboard
muestra el resto plegado como “Experimentales”). La curación se basa en fiabilidad medida
(sesión E2E del 2026-10-08), no en opinión; los presets experimentales siguen en el catálogo
porque son la cobertura viva del motor (`edd` es el único usuario de producción de
`until.metric`).

| Preset core | Descripción |
|--------|-------------|
| **tdd** | Loop TDD con ruta de test declarada por el preset: rojo &rarr; implementar &rarr; `until` verde sobre ese archivo. |
| **development** | Decompose &rarr; subtareas secuenciales (test-first, `verdict` por subtarea) &rarr; verificación del agregado acotada a lo que escribió el run. |
| **bdd** | List-driven: historias &rarr; mini-TDD por ítem sobre `tests/test_historia_$iter.py`. |
| **collaborative** | Debate de panel multi-agente con consenso del moderador; el único preset sin proyecto. |
| **sdd** | Spec-first: spec &rarr; gates de bloqueantes &rarr; implementación &rarr; trazabilidad. |

| Experimental | Por qué no es core |
|--------|-------------|
| **edd** | Loop de métrica de evaluación (`until.metric`) sobre un archivo de evals sembrado por el preset; los modelos más débiles/Ollama pueden dejar el esqueleto incompleto, por eso el preset lo siembra de forma determinista. |
| **coordinated** | `development` + fan-out paralelo; sus subtareas corren en paralelo, así que solo es fiable cuando cada una es dueña de archivos disjuntos (cierra Alta en módulos independientes) y degrada cuando comparten un módulo/`__init__.py` (gana la última escritura y el gate de un hermano queda rojo). Sigue experimental hasta que el decompose pueda garantizar la propiedad de archivos por subtarea. |
| **domain_tdd** | `bdd` + un paso de modelo de dominio; históricamente el preset que más tokens consume. |
| **reflexion** | Loop generador &rarr; crítico; a menudo el más caro por entrega, mientras `collaborative` cubre el arquetipo de debate. |
| **long_horizon** | `sdd` con techo de 1,5M, 10 ítems y un loop de 12 iteraciones para tareas largas; experimental porque es caro y su smoke todavía muestra degradaciones de proceso. |

Inspecciona y valida los presets con el CLI:

```bash
uv run python -m orchestration.dsl.cli list
uv run python -m orchestration.dsl.cli validate tdd --conformance   # valida Y ejecuta contra el motor real
uv run python -m orchestration.dsl.cli new --type=tdd               # genera un workflow nuevo
```

Además existe el **fast path de comando directo de tool** (`tool_name: action, key=val`) para
runs de una sola tool, y el **chat canónico de Bot Mode**, donde las conversaciones de un bot
las sirve `bots_runner` con la identidad del bot &mdash; sin workflow de por medio.

---

## Funciones destacadas

- **Maestro multi-sesión** &mdash; N sesiones concurrentes de chat/workflow (por defecto 4, `MAESTRO_MAX_SESSIONS`) con aislamiento por run: callbacks de aprobación, presupuestos de tokens y seguimiento de colisiones de escritura con scope ContextVar por run.
- **Bot Mode** &mdash; bots de IA persistentes con identidad definida en YAML (`templates/bots/`): DMs async 1:1 entre bots, salas de grupo compartidas con topes de turnos y rutinas temporales (programadas a una conversación dedicada o entregadas como turno real del bot).
- **Pausas y clarificaciones persistidas** &mdash; los agentes pueden detenerse a mitad de workflow y preguntar al usuario (`ask_clarification`); el estado sobrevive reinicios y reanuda en el paso exacto (pausas DSL y de bot).
- **Project Knowledge Base (PKB)** &mdash; conocimiento markdown curado por workspace (`workspaces/<ws>/knowledge/`), consultable con la tool `project_docs` (`list/read/search/inject`).
- **Skills procedimentales** &mdash; carga de skills bajo demanda (`load_skill`) con gate de aprobación de skills locales (`core/skills_approval.py`): las skills de workspace no aprobadas quedan ocultas para los agentes hasta que se aprueban.
- **Visor independiente** &mdash; `viewer/viewer.py` renderiza markdown/HTML (sanitizado)/PDF/texto de forma segura; la tool `file_view` abre archivos ahí sin que el contenido vuelva al agente.

---

## Agentes (5 perfiles)

| Agente | Rol | Tools core | Ideal para |
|-------|------|------------|----------|
| **developer** | agent | file_manager, git_manager, bash_manager, lsp_manager, code_exec, test_runner, diff_editor, file_view, tools goal/todo/plan, delegate_task | Programar, construir, probar |
| **analista** | analysis | file_manager (lectura), lsp_manager, code_search, file_view, test_runner, pdf_read, plan_mode/exit_plan_mode | Análisis, revisión, diseño de arquitectura |
| **moderador** | reasoning | ninguna | Moderación de debates, consenso |
| **conversacional** | agent | ninguna | Chat rápido, agente de fallback |
| **test_author** | testing | file_manager, file_view | Escribir tests de aceptación antes de la implementación |

Defaults: `DEFAULT_AGENT=developer`, `FALLBACK_AGENT=conversacional`. La lista completa de
tools por perfil vive en `templates/agents/*.yaml`.

---

## Tools (24 registradas)

Se registran con `@tools_registry.register("name")` y se describen como specs de
function-calling de OpenAI en `tools/specs.py` (23 entradas en `TOOL_DEFINITIONS`). El
**nombre registrado** puede diferir del nombre del archivo.

| Nombre registrado | Archivo fuente | Acciones / propósito |
|-----------------|-------------|-------------------|
| `file_manager` | `file_manager.py` | `write` / `read` / `append` / `delete` |
| `bash_manager` | `bash_manager.py` | Ejecuta comandos de shell (`command`, `cwd`, `timeout`); sanitizado con blocklist |
| `git_manager` | `git_manager.py` | `init` / `add` / `commit` / `log` / `diff` / `show` |
| `test_runner` | `test_runner.py` | Ejecuta suites de test (pytest, etc.) |
| `lsp_manager` | `lsp_manager.py` | `definition` / `hover` / `diagnostics` / `references` / `ruff_check` (jedi) |
| `code_exec` | `code_execution.py` | Ejecuta Python en un sandbox de RestrictedPython dentro de un subproceso confinado |
| `diff_editor` | `diff_editor.py` | Aplica/crea diffs unificados (contenidos en el workspace) |
| `code_search` | `code_search.py` | Búsqueda por patrón en el código |
| `pdf_read` | `pdf_reader.py` | Extrae texto de PDFs (pdfplumber) |
| `memory_inspector` | `memory_inspector.py` | Inspecciona y gestiona la memoria persistente |
| `file_view` | `file_viewer.py` | Abre un archivo en el visor independiente (el contenido nunca vuelve al agente) |
| `project_docs` | `project_docs.py` | Project Knowledge Base: `list` / `read` / `search` / `inject` |
| `vision_analyze` | `vision_analyze.py` | Análisis de imágenes con el rol de modelo `vision` dedicado |
| `load_skill` | `skill_loader.py` | Carga una skill procedimental bajo demanda (las skills de workspace requieren aprobación) |
| `goal_create` / `goal_get` / `goal_update` / `goal_round` | `goal_todo.py` | Seguimiento de objetivos y rondas de iteración |
| `todo_write` / `todo_get` | `goal_todo.py` | Gestión de listas de tareas |
| `plan_mode` / `exit_plan_mode` | `goal_todo.py` | Modo de planificación explícito antes de implementar |
| `delegate_task` | `delegation.py` | Delega una subtarea a subagentes en proceso con contexto aislado |

> `ask_clarification` (`ask_clarification.py`) es la tool registrada número 24 pero **no** está
> en `TOOL_DEFINITIONS` (23 specs); se intercepta directamente en el agent loop
> (`orchestration/loop.py`) en vez de invocarse por function-calling, y pausa el workflow con
> una pregunta persistida.

La extensibilidad adicional vive en `tools/kits/` y `tools/skills/`.

---

## Infraestructura en detalle

### Subsistemas de `core/`

- **Circuit breaker** (`circuit_breaker.py`) &mdash; por proveedor, closed/open/half-open; se abre tras fallos consecutivos y hace fallback a Ollama. Protege `call` y `call_stream`.
- **Rate limiter** (`rate_limiter.py`) &mdash; ventana deslizante con cuotas por minuto y por hora.
- **Memoria** (`core/memory/manager.py`, `core/faiss_indexer.py`, `core/embedding_provider.py`) &mdash; búsqueda vectorial FAISS + `MemoryManager` con caché LRU de embeddings en proceso. El self-healing (`self_healing_check()`, ejecutado por el loop de `DAEMON_MODE`) usa `SELF_HEAL_INTERVAL` (120s por defecto): crítica de calidad, detección de duplicados (similitud FAISS > 92%), resolución de contradicciones y poda (sin acceso durante 30+ días).
- **Change tracker** (`change_tracker.py`) &mdash; undo/redo de operaciones de archivo vía `.undo`/`.redo`.
- **MCP** (`mcp/`) &mdash; cliente del Model Context Protocol (conecta servidores externos) + servidor (`uv run python -m core.mcp.server`) que expone las **23** tools de function-calling de `TOOL_DEFINITIONS` por stdio JSON-RPC; `ask_clarification` es solo intercepción y no se expone.
- **Sandbox** (`sandbox/`) &mdash; ejecutor de RestrictedPython en un **proceso hijo confinado** (`sandbox/runner.py`): el hijo nunca importa `core.*` (sin settings ni secretos en su memoria), la memoria se limita en el hijo (`RLIMIT_AS`), los timeouts matan al hijo (`SIGKILL`) y la concurrencia está acotada (`MAX_CONCURRENT_CHILDREN`).
- **Seguridad** (`security/`) &mdash; undercover mode, anti-distillation (watermarks rotativos, detección de patrones, escalado warn&rarr;throttle&rarr;honeypot&rarr;lock) y detector de frustración.
- **Health** (`health.py`) &mdash; `run_health_check()` emite **6 filas**: Database, LLM, Memory Dir, Templates, Workspace, Embeddings.
- **Presupuesto de tokens y caché** (`token_counter.py`, `cache_manager.py`, `context_manager.py`) &mdash; la compresión se dispara al 90% de `MAX_CONTEXT_TOKENS` y apunta al 70%; `cache_manager` es telemetría de prompt-cache, no una caché.
- **Hooks** (`hooks_registry.py`, `hook_loader.py`, `hooks/`) &mdash; registry genérico; los **6** puntos de intercepción que despacha `tools/orchestrator.py` alrededor de cada tool son `on_before_tool`, `on_after_tool`, `on_tool_error`, `on_permission_denied`, `on_token_budget_exceeded` y `on_tools_disabled`.
- **Bootstrap / config / paths** (`bootstrap.py`, `config.py`, `path_resolver.py`, `feature_flags.py`) &mdash; arranque, pydantic-settings, resolución de paths (**nunca hardcodees paths &mdash; usa `core.path_resolver.paths`**) y feature flags.

### Capa `llm/`

- **Configuración por rol** &mdash; `settings.model_roles` mapea roles (`default`, `fast`, `reasoning`, `agent`, `creative`, `critique`, más los roles dedicados `vision` y `deepinfra`) a proveedor/modelo/temperatura. Todos usan DeepInfra `deepseek-ai/DeepSeek-V4.1-Flash-1017` por defecto (configura `DEEPINFRA_API_KEY`).
- **Proveedores** &mdash; DeepInfra (por defecto del producto), DeepSeek/OpenAI (cliente compatible con OpenAI), pool Grok/NVIDIA y Ollama. Hace fallback a Ollama cuando `OFFLINE_MODE=true` o falla el chequeo de conectividad.
- **Strict mode** &mdash; `DEEPSEEK_STRICT_MODE` (por defecto **false**) activa `strict=true` + `additionalProperties=false` en schemas de tools que no son MCP para forzar el cumplimiento de `required`.
- **Parser / prompts / offline** &mdash; parseo de respuestas, armado de prompts y orquestación offline.

### Ejemplo de hook

```python
from core.hooks_registry import hooks_registry, HookContext

@hooks_registry.register("on_before_tool")
def my_hook(ctx: HookContext) -> None:
    print(f"A punto de ejecutar: {ctx.tool_name}")
```

### MCP &mdash; conectar servidores externos

Edita `workspaces/<name>/mcp_servers.json` (o el global). Las tools se registran como
`mcp:<prefix>.<name>`:

```json
[
  {
    "name": "example",
    "command": "example-mcp-server",
    "args": [],
    "tools_prefix": "example",
    "enabled": true
  }
]
```

Ejecuta Morphix como servidor MCP: `uv run python -m core.mcp.server` (expone las 23 tools de
function-calling; `ask_clarification` es solo intercepción y no se expone).

> **Nota:** las entradas de `[project.scripts]` en `pyproject.toml` (`morphix-mcp`,
> `morphix-workflow`, `morphix-curator`) **no se instalan** porque el proyecto usa
> `[tool.uv] package = false`. Invoca siempre con `uv run python -m ...` como se muestra
> arriba.

---

## Configuración

Variables clave del `.env` (ver `example.env` y `core/config.py`):

```bash
# Obligatorio
DATABASE_URL=postgresql://user:pass@localhost:5432/morphix
DEEPINFRA_API_KEY=                  # proveedor por defecto (se requiere al menos una clave LLM)

# LLM opcional
DEEPSEEK_API_KEY=                   # proveedores alternativos (también pool NVIDIA / GROK / OPENAI)
OPENAI_API_KEY=                      # también se soporta GROK_API_KEY
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:120b-cloud
LLM_TIMEOUT=60
DEEPSEEK_STRICT_MODE=false
MAX_CONTEXT_TOKENS=128000

# Seguridad (ENCRYPTION_KEY se autogenera en dev; OBLIGATORIA en producción)
ENCRYPTION_KEY=
PASSWORD_HASH=                      # gate de login opcional de la GUI

# Workspace / agentes
ACTIVE_WORKSPACE=main
DEFAULT_AGENT=developer
FALLBACK_AGENT=conversacional
DEFAULT_WORKFLOW=development

# Feature flags
DARK_MODE=true                       # true = oscuro (papel); false = claro (papel claro)
OFFLINE_MODE=false
UNDERCOVER_MODE=true
DAEMON_MODE=false                    # daemon de heartbeat/self-heal (false por defecto; true en example.env)
SELF_HEAL_INTERVAL=120
CONTEXT_COMPRESSION=true
MAX_SUBTASKS=8
MAX_AGENT_ITERATIONS=8
TOOLS_ENABLED=true
ALLOW_CODE_EXECUTION=true

# Ajustes de tools
TOOL_MAX_TOKENS_PER_WORKFLOW=80000
TOOL_ENABLE_TOKEN_BUDGET=true
TOOL_MAX_RETRIES=3
TOOL_BACKOFF_BASE=1.5

# Pool de base de datos
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_PRE_PING=true
DB_POOL_RECYCLE=3600
```

> `ENCRYPTION_KEY` se autogenera en dev pero **lanza `ValueError` en producción**
> (`MORPHIX_ENV=production`). CI define `UNDERCOVER_MODE=false`, `DAEMON_MODE=false` y
> `OFFLINE_MODE=true` para desactivar funciones que necesitan auth o servicios externos.

---

## Comandos

| Tarea | Comando |
|------|---------|
| Ejecutar GUI | `uv run python run.py` |
| Ejecutar servidor MCP | `uv run python -m core.mcp.server` |
| CLI del DSL de workflows | `uv run python -m orchestration.dsl.cli new\|validate\|list` |
| Validar preset + conformance | `uv run python -m orchestration.dsl.cli validate tdd --conformance` |
| Todos los tests (async + coverage) | `uv run pytest` |
| Un test | `uv run pytest tests/test_dsl_dispatch.py::TestDirectToolConDSL::test_direct_tool_respeta_ruta` |
| Lint | `uv run ruff check .` |
| Formato | `uv run black .` |
| Tipado | `uv run mypy core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/` |
| Pre-commit (todos los hooks) | `uv run pre-commit run --all-files` |
| Migraciones de BD | `uv run alembic upgrade head` |
| Health check | `uv run python -c "import asyncio; from core.health import run_health_check; r = asyncio.run(run_health_check()); print(r.format())"` |
| Documentación (local) | `uv run mkdocs serve` |

**Orden de chequeo local:** `ruff check .` &rarr; `black --check .` &rarr; `mypy` &rarr; `pytest`.

> `run.py` carga el `.env` completo. `pytest` no carga todo el archivo, pero
> `tests/conftest.py` lee **solo `DATABASE_URL`** del `.env` cuando no está ya en el entorno,
> así que los tests `_pg` corren sin export manual cuando existe `.env`. Las API keys nunca
> se cargan en el proceso de test. Si no existe `.env`, expórtalo explícitamente:
> `export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)`.

---

## Documentación

La documentación completa se construye con **MkDocs Material** y está disponible en el
[sitio de Morphix](https://morphilab.github.io/morphix). Para verla en local:

```bash
uv sync --group docs
uv run mkdocs serve
```

La documentación cubre:

- **Getting Started** &mdash; instalación, configuración, primer workflow
- **User Guide** &mdash; panorama de la GUI, workflows, agentes, tools, workspaces
- **Bot Mode** &mdash; bots, DMs, salas de grupo, rutinas (`docs/bot-mode.md`)
- **Architecture** &mdash; decisiones de diseño, flujo de datos, sistema de workspaces, modelo de seguridad, sistema de memoria, integración MCP y detalle por capa
- **Developer Guide** &mdash; agregar tools, agentes, workflows y hooks; contribuir; guía de testing
- **API Reference** &mdash; autogenerada desde docstrings con mkdocstrings
- **Changelog** &mdash; notas de release e historial de versiones

---

## Estructura del proyecto

```
morphix/
├── core/                 # Lógica de negocio (sin dependencias de UI)
│   ├── bots*.py          # Dominio Bot Mode (identidad, DMs, grupos, rutinas, protocolo)
│   ├── mcp/              # Protocolo MCP (cliente + servidor)
│   ├── memory/           # MemoryManager + self-healing (FAISS vive en core/faiss_indexer.py)
│   ├── sandbox/          # RestrictedPython en un runner de subproceso confinado
│   ├── security/         # undercover, anti-distillation, detector de frustración
│   ├── hooks/            # implementaciones de hooks globales
│   └── repositories/     # Repositorios de BD (ConversationRepository, ...)
├── llm/                  # controller, provider, parser, prompts, offline
├── agents/               # registry, loader, perfiles, base, service, audit
├── tools/                # 23 specs (+ ask_clarification), registry, orchestrator, wrapper, loader, kits, skills
├── orchestration/        # agent loop, decomposer, aggregator, finalizer, emitter, runtime Bot Mode
│   ├── dsl/              # DSL de workflows: schema, compiler, validator, engine, registry/plugins, cli, conformance
│   ├── workflows/        # orchestrator.py (routing: direct-tool / bots / DSL)
│   └── bots_*.py         # runtime de bots (wake, clock, groups_drive, runner, dispatch)
├── desktop/              # GUI PySide6
│   ├── services/         # config, dashboard, historial, workflow_runner, proyecto, memoria, ...
│   └── widgets/          # widgets reutilizables
├── research/             # harness de evaluación independiente (batch runner + compresor de trayectorias)
├── viewer/               # visor de archivos independiente (cero imports de Morphix)
├── templates/            # agentes/, workflows/, bots/ YAML + skills/ Markdown
├── workspaces/           # configuración por workspace + datos runtime (gitignored)
├── docs/                 # fuente de documentación MkDocs
├── alembic/              # migraciones de BD
├── tests/                # 295 módulos de test (2.719 funciones, medido el 2026-11-11)
└── logs/                 # logs runtime (morphix.log)
```

---

## Desarrollo

- Consulta el [sitio de documentación](https://morphilab.github.io/morphix) para arquitectura, workflows, agentes, tools y guías de desarrollo.
- Consulta [docs/](docs/) para la fuente completa de la documentación.
- Consulta [CONTRIBUTING.md](.github/CONTRIBUTING.md) para setup y convenciones de desarrollo.

**Convenciones de test:** `pytest-asyncio` con `asyncio_mode = "auto"`; marca los tests async
con `@pytest.mark.asyncio`. `conftest.py` define fixtures **autouse** compartidas que resetean
el estado global después de cada test (engine de BD, singletons globales, censo de hilos) y
carga solo `DATABASE_URL` desde `.env` cuando falta en el entorno (las API keys nunca se
cargan). Los mocks se definen inline por módulo. CI provee PostgreSQL como contenedor de servicios.

---

## Licencia

MIT &mdash; ver [LICENSE](LICENSE) para el texto completo.
