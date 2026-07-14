# Changelog — Morphix

## Fix: TDD green-field (detección por filesystem) + pytest rootdir (2026-06)
- **2.º fallo TDD:** tras arreglar `project_root`, el ciclo seguía sin producir nada. La detección green-field buscaba marcadores ("no tests ran"…) en el output de `test_runner`, pero `tool_orchestrator` **trunca ese output a 300 chars** y pytest pone "no tests ran" **al final** (tras el banner + `rootdir: …/codemorphix` de morphix). → `no_tests=False` → prompt de "corregir fallos" → el agente intentaba **leer** `"."` (directorio) y se estancaba sin escribir.
- **Fix:** `tdd.py` detecta green-field **escaneando el directorio del proyecto** por `test_*.py`/`*_test.py` (señal fiable). `test_runner.py` corre pytest con **`--rootdir=<proyecto>`** + `-p no:cacheprovider` (usa la config del proyecto, no la de morphix). `file_manager.py` `action=read` sobre un **directorio** devuelve el **listado** en vez de `FileNotFoundError`. +4 tests.

## Fix: workflow TDD (project_root) + green-field + loader (2026-06)
- **TDD no producía nada:** `_dispatch_route` pasaba a `_run_tdd_loop` el `project_root` derivado de la plantilla (`None`, porque no existe `tdd.yaml`) en vez de `ctx.project_root`. Resultado: `test_runner` buscaba en `memory/main/tests` y el agente recibía `Ruta no permitida: /` → stall, proyecto vacío. Ahora la ruta TDD usa **`ctx.project_root`** (el proyecto activo de la GUI).
- **Green-field:** `execute_tdd_loop` usa `file_path="."` (descubre tests en el proyecto) y, cuando no hay tests aún, instruye al agente a **escribir** tests + implementación (rutas relativas), en lugar de "corregir fallos" inexistentes.
- **Agente fantasma:** `agents/loader.py` ahora ignora archivos `_`-prefijados → `_FULL_TEMPLATE.yaml` ya no se registra como `mi_agente`.
- +3 tests. (El resto de pruebas — bash/code_exec/diff_editor/streaming — ya verificadas como funcionando: `test_lab` con app.py/tests/src/ + node_modules.)

## Fix: bash en chat + code_exec output + diff_editor (2026-06)
- **bash sin salida en modo Chat:** `execute_agent_loop` no recibía el puente de eventos en chat (`_run_direct_agent` solo pasaba `on_stream_chunk`), así que el emit `[bash_manager]` (condicionado a `events`) nunca llegaba a la pestaña Bash. Ahora `execute_agent_loop` acepta `events` y el chat pasa `build_workflow_events()` → bash/sistema/stats se muestran (sin duplicar el streaming).
- **code_exec "sin salida":** el sandbox solo capturaba `print()`. Ahora también evalúa el **valor de la última expresión** (estilo REPL); `np.mean(arr)` sin `print` ya muestra resultado. + nudge en el prompt. +3 tests.
- **diff_editor "no disponible":** el agente (copia workspace) lo tenía, pero la plantilla `development.yaml` lo filtraba (`tools.allowed`). Añadido a `development.yaml` (template+workspace) y a `templates/agents/developer.yaml` (sincronizado con la copia workspace).
- Suite completa verde (684/0); ruff/black/mypy limpios.

## Feature: tab Editor (visualizador/editor de archivos) (2026-06)
- Nuevo tab **Editor** (reemplaza el placeholder *Integraciones*): árbol del directorio del proyecto activo (`QTreeView` + `QFileSystemModel`, oculta ruido `.git`/`__pycache__`/`.codebase_cache`/…, auto-refresca cuando el agente crea archivos) + editor de texto (`QPlainTextEdit`).
- **Detecta el proyecto activo** vía nueva señal `project_changed` (emitida por `MaestroTab` al cambiar de proyecto).
- **Operaciones:** abrir/ver, editar y **guardar** (escritura directa, sin bloquear por sintaxis, con guarda de que la ruta está dentro del proyecto), **crear** archivo/carpeta, **renombrar**, **eliminar** (menú contextual + barra). Layout fijo (árbol ~280px + editor flexible). Sin resaltado de sintaxis (v1).
- Archivos: `desktop/editor_tab.py` (nuevo), `desktop/events.py` (señal), `desktop/maestro_tab.py` (emite), `desktop/main_window.py` (tab + wiring). Sin cambios en `core/`. Verificado con smoke test offscreen (abrir/guardar/crear/renombrar/eliminar + guarda de seguridad).

## Fix: streaming tool-call argument accumulation (2026-06) — causa raíz del "no genera nada"
- **Bug:** en streaming OpenAI/DeepSeek, solo el **primer** delta de una tool call trae `id`+`name`; los siguientes traen **`id=None`** y solo fragmentos de `function.arguments`, asociados por **`index`**. `_stream_openai_async` (`llm/controller.py`) generaba un id sintético distinto por cada fragmento (`call_1`, `call_2`, …) en vez de asociarlos por `index`, así que `_accumulate_stream` (`orchestration/loop.py`) los **descartaba** → `arguments` vacío (`{}`) → `file_manager` sin `path`/`content` → `"Agent stalled: 2 iterations without file modifications."` **sin escribir nada**. En la GUI solo se veía la intención transmitida.
- **Evidencia:** `memory/logs/audit.jsonl` mostró que `file_manager` recibía `params` solo con `project_root`+`workspace` (ambos añadidos por el sistema), confirmando argumentos vacíos del modelo.
- **Fix:** acumular las tool calls por `index` y reemitir cada `StreamChunk` con el **id real** de la llamada → los argumentos se reconstruyen completos. +2 tests de regresión (`tests/test_llm_stream_tool_calls.py`).
- Solo afectaba al **streaming** (chat/agent loop); el camino no‑streaming (`call`) ensambla los tool_calls vía SDK y nunca tuvo el bug.

## Fix: file_manager action inference (2026-06)
- Medida defensiva complementaria (no era la causa raíz; ver entrada anterior): `file_manager_tool` **infiere la acción** cuando falta (`write` si hay `content`, `read` si no) y `_is_modifying_action` lo reconoce como modificación. +4 tests.

## GUI: static cockpit + responsiveness (2026-06)
- **Maestro static 3-column cockpit** (`desktop/maestro_tab.py`): replaced the user-draggable 4-pane `QSplitter` and the agent-panel collapse/expand churn with a fixed layout — top bar (estado · modo · proyecto · agente + acciones) + **Ejecución** column (progreso, stats, subtareas, archivos) + flexible **Conversación** + static **Detalle** `QTabWidget` (Agentes / Diagrama / Log / Bash). Agent picker is now a combo in the top bar; Chat/Orquestar is behavior-only (no layout change).
- **History tab** (`desktop/history_tab.py`): replaced its `QSplitter` with a fixed two-column layout.
- **Responsiveness fixes**:
  - `ChatBubble` streaming debounce (~70ms) + cached browser reference — removes per-token O(n**2) `setMarkdown` re-renders (`desktop/widgets/chat_bubble.py`).
  - Status log now appends in O(1) with a 400-block cap (was full `toHtml()`+`setHtml()` per message).
  - Stats panel uses differential updates (only writes changed labels/lists/progress).
  - Diagram re-renders only when the HTML changes; snapshot disk write moved off the event loop (`orchestration/diagram.py`).
  - Removed all mid-workflow `QSplitter.setSizes()` churn.
- Verified: ruff/black clean, mypy 0 errors, offscreen construction smoke tests for both tabs, full suite unchanged (675 pass / 1 known environmental flake).

## DB engine loop-hardening + flake diagnosis (2026-06)
- **Loop-aware async engine** (`core/database.py`): `_get_async_engine()` recreates the engine (and a lazy, loop-aware schema lock) when the running event loop changes, preventing cross-loop reuse of asyncpg connections. `get_async_session_factory()` rebuilds after a loop change; `dispose_engine()` clears the tracked loop.
- **Per-test engine isolation** (`tests/conftest.py`): autouse fixture resets the global engine after every test.
- **Diagnosed the `test_development_route` full-suite flake**: it is **not** a DB/product bug. pytest-asyncio function-scoped loops create a fresh epoll fd per test; across ~676 loops the churn intermittently corrupts the process epoll/fd state → `EpollSelector.poll()` raises `OSError: [Errno 22]`. Documented as an environmental test-infra artifact (passes in isolation). A session-scoped test loop would remove the churn (deferred — changes isolation for all tests).

## Test stabilization (2026-06)
- **Fixed 3 stale tests** (test-only; production code was correct):
  - `test_decompose_with_kwargs` — pass `project_context` (decompose prompt placeholder; `decomposer.py` already passed it).
  - `test_context_snapshot` — populate `_phases` (sprint-25 storage; `_data` obsolete).
  - `test_coordinated_workflow_e2e` — mock `decompose_task_with_phases` (sprint-25 added phase decomposition before the DAG path).
- **Fixed 1 test-isolation bug**: `test_get_encoding_loads_and_caches` now resets the global `token_counter._enc` at start (it was order-dependent).
- **Full suite: 675 pass / 1 environmental flake** (`test_development_route` — `OSError` under full-suite load from asyncpg/event-loop resource accumulation; passes in isolation).

## Hotfix: workflow consistency + architect agent (2026-06)
- **architect agent**: new `templates/agents/architect.yaml` (+ `workspaces/main` copy) — read-only design/architecture profile (`type: analysis`, `model_role: reasoning`)
- **coordinated.yaml** (both copies): added `architect` to `agents.allowed` so the `default_phases` design phase resolves; added `architect` to the `agent_hint` list in `coordinated.py`
- **development.yaml**: removed the phantom `browser` tool from the global template (not a registered tool)
- **orchestrator.py**: `_run_simple_conversation` now uses `get_global_workspaces().current` instead of hardcoded `"main"` (fixes non-`main` workspaces)
- **mypy 0 errors restored**: fixed pre-existing `core/health.py` (`r.ping()` await) and `orchestrator.py` `PausedSession` query type errors
- **guard test**: `tests/test_template_consistency.py` — every agent/tool referenced by a workflow template must exist (prevents recurrence)
- **Discovered (open)**: 3 pre-existing test failures unrelated to these fixes — see `PENDING.md` / README "Known Issues"

## Sprint 26b: pending-items-cleanup (2026-06)
- **Skills/kits deployment**: `tools/skills/` (8 YAML) + `tools/kits/` (5 YAML) wired
- **files_written indicator**: enriched `emit_stats` payload + dashboard wiring; `_on_stats` tolerates `files_written` as `int` or `list`
- **Safety Net respects agent type**: analysis agents (analista) never fabricate files
- **HTML export**: `html` format in `ConversationRepository.export()` with pygments highlighting (lazy import + fallback)
- **Smart decomposition**: context-aware prompt with real project detection
- **max_agent_iterations configurable**: via `.env`, template and `core/config.py` (default **8**)
- **Maintenance**: batch-rebuild FAISS + progress-bar visibility + cancelled-error handler; mypy `progress_callback` annotation; missing `asyncio` import in `maestro_tab.py`; `workspaces/main` ↔ `templates/` sync

## Sprint 26: remaining-fixes-polish (2026-06)
- **Export fixes**: `project_path` scoped to current project (no cross-project contamination); dev agent messages included in exports
- **Agent role regression**: keep `agent`-role messages out of LLM history (0 `unknown variant agent`)
- **tool_call_id filtering**: drop tool messages missing `tool_call_id` before the LLM call (0 `missing field tool_call_id`)
- **lsp_manager NoneType**: `issue.get("fix") or {}` guard
- **GUI**: status log vs diagram split (two `QTextBrowser` in a `QSplitter`)

## Sprint 25: blackboard-multi-phase (2026-05-28)
- **Phase-aware blackboard**: `SharedBlackboard` rediseñado con namespaces por fase (`write/read/read_phase/list_phases`), `snapshot/restore()` para pause/resume, `sync_to_db/from_db()` para persistencia PostgreSQL (`BlackboardEntry` model), `get_cross_phase_context()` para inyectar contexto de fases anteriores en agentes de la fase actual. 20 tests.
- **Phase-aware decomposition**: Nuevo `decompose_task_with_phases()` que agrupa subtareas en fases lógicas (design, implement, test, verify). Nuevo prompt `DECOMPOSE_TASK_WITH_PHASES_PROMPT`. Rate limiter awareness integrado (remaining < 5 → máximo 2 fases). Fallback a single-phase si el LLM no produce fases. 4 tests.
- **Coordinated multi-phase execution**: `_run_coordinated()` prueba `decompose_task_with_phases()` primero. Si hay múltiples fases, ejecuta fase por fase con blackboard y persiste tras cada fase. Si no, usa el DAG original (backward compatible).
- **Development workflow blackboard**: `_run_full_orchestration()` crea `SharedBlackboard` asignado a `WorkflowContext.blackboard`. Subtareas escriben resultados al blackboard y reciben contexto de subtareas anteriores mediante `execute_subtask_safe()` → `extra_context`.
- **Pause/resume blackboard persistence**: Pausa guarda `blackboard_snapshot` en `paused_data`. Resume restaura el blackboard desde `ctx.blackboard.restore()`.
- **Workflow template**: `coordinated.yaml` con `phases_enabled: true`, `max_phases: 4`, `default_phases` con agentes por fase.

## Sprint 24: stabilization (2026-05-28)
- **diff_editor `content` alias**: Añadido `content` como alias de `diff_content` (DeepSeek envía `content=` en vez de `diff_content=`). Mismo patrón que `path`→`file_path`. 3 tests.
- **auto-commit rate limit validation**: `_generate_commit_message()` detecta respuestas de error del rate limiter (empiezan con `❌`, contienen "rate limit") y usa fallback `feat: {task}` en vez de commitear el error. Defensa en profundidad en `git_manager` — rechaza mensajes que empiezan con `❌`. 4 tests.
- **test_runner false negatives**: `_test_runner_tool()` usa `_parse_pytest_counts()` para determinar éxito en vez de `returncode == 0`. Si pytest corrió tests y todos pasaron, éxito aunque returncode != 0. 3 tests.
- **bash_manager path hallucination fast-fail**: Bloquea paths alucinados `/root/workspace`, `/root/.openclaw` en `_sanitize_command()` con mensaje instructivo. 3 tests.
- **python3 -c improved block message**: Mensaje de bloqueo ahora sugiere alternativas (`file_manager`, `bash_manager`, `test_runner`). 1 test.
- **orchestrator result-based fast-fail**: Añadido fast-fail para "file not found" en el path de reintento basado en resultados (Path B). 2 tests.
- **rate limiter awareness**: `RateLimiter.remaining()` expone slots disponibles. `decompose_task()` limita a 2 subtareas cuando `remaining < 10`. 3 tests.
- **Export HTML**: Nuevo formato `html` en `ConversationRepository.export()`. Syntax highlighting con pygments, CSS inline, self-contained HTML.

## Sprint 23: dev-dashboard (2026-05-27)
- **QProgressBar**: añadida al panel derecho de stats, muestra completadas/total
- **QListWidget**: lista de subtareas en panel izquierdo con iconos de estado (✅🔵❌⏳)
- **`_build_subtask_list()`**: helper que genera `{name, status}` desde resultados del DAG
- **emit_stats**: payloads enriquecidos con `subtask_list` en decompose, por-subtarea, y final

## Sprint 22: conversation-continuity (2026-05-27)
- **load_conversation**: ahora incluye mensajes agent/tool en `_history` (antes los filtraba)
- **WorkflowContext.is_follow_up**: nuevo flag, seteado por GUI cuando `conversation_id` está presente
- **decompose_task**: inyecta contexto de continuación — le dice al LLM que el proyecto ya existe
- **TaskAnalyzer**: recibe `is_follow_up`, usa cache key separada, sugiere menor complejidad
- **ConversationRepository.save()**: resume path ahora guarda TODOS los agent/tool, elimina side-channel frágil
- **Tests**: 6 nuevos tests para decompose follow-up, save resume, context flag

## Sprint 21: clarification-requests (2026-05-27)
- **Nuevo tool**: `tools/ask_clarification.py` — el agente puede preguntar al usuario mid-workflow
- **Agent loop**: intercepta `ask_clarification` antes de ejecución, retorna dict de pausa
- **Subtask executor**: propaga status `clarification_needed` hacia arriba sin completar la subtarea
- **Orquestrador**: `_save_paused_session()` + `resume_workflow()` con restauración de estado completo
- **WorkflowContext**: campo `last_clarification` para pasar la pregunta al GUI
- **Modelo**: tabla `PausedSession` para persistencia cross-session de pausas
- **GUI**: detecta `[PAUSED:clarification_needed]`, muestra pregunta, redirige input a resume
- **Tests**: 8 nuevos tests (registro de tool, intercepción en loop, contexto, modelo)

## Sprint 20: final-polish (2026-05-27)
- **Export mejorado**: strip watermarks del contenido exportado, incluye archivos del disco al final del export
- **diff_editor path alias**: acepta `path=` como alias de `file_path=` para compatibilidad con LLM
- **Watermark skip flag**: `skip_watermark=True` en `get_safe_response()` y `add_watermark()` para exports
- **Development decomposer**: pide 3-5 subtareas granulares en vez de 1-4 monolíticas
- **SAFE_MODULES**: `ast` y `io` agregados al sandbox
- **AGENTS.md**: limpieza de historial de sprints, resumido a un párrafo
- **CHANGELOG.md**: creado con historial completo de 20 sprints

### Validación post-deploy (May 27, conv 5-6)
- **2 conversaciones, workflow development**: web_scraper + backup_automator
- **0 crashes, 0 ERROR logs, 0 circuit breaker trips**
- **bash_manager failures**: 0 reales (los 4 anteriores eran contexto de prompt, no ejecuciones)
- **python -c blocked**: 0 | **python: not found**: 0
- **code_exec/sqlite3 errors**: 0
- **Safety Net**: 7/8 (87.5%) — 1 fallo en archivos no-.py (limitación conocida)
- **Watermarks en exports**: 0 — fix confirmado
- **Export con archivos del disco**: presente en ambos exports
- **Decomposer**: 3 y 5 subtareas — prompt nuevo funcionando
- **Supervisor**: 0 analyst en nivel 1
- **Código generado**: web_scraper (52 líneas, OK) + backup_automator (260 líneas, 7 archivos, OK)
- **Graceful shutdown**: confirmado

## Sprint 19: safety-net-reliability (2026-05-27)
- **bash_manager wrapper fast-fail**: `tools/wrapper.py` verifica `command` vacío antes de llamar al tool, sin 3 reintentos
- **Supervisor analyst preservation**: el supervisor ya no fuerza `developer` en lugar de `analyst` para verificación
- **Safety Net WARNING logs**: `DEBUG → WARNING` en fallos de JSON y excepciones, mejor visibilidad
- **SAFE_MODULES `sqlite3`**: agregado a `restricted_executor.py`

## Sprint 18: export-and-skill-fixes (2026-05-26)
- **Aggregator**: lee archivos reales del disco en vez de contenido truncado de DB
- **subtask.py**: elimina truncación `content[:200]` — código completo llega al aggregator
- **lsp_manager**: `issue.get("location") or {}` maneja claves con valor `null` en JSON de ruff

## Sprint 17: tool-kits (2026-05-26)
- **Tool kits**: `tools/kits/` — 5 YAML con workflows multi-tool (code_quality, debug_cycle, project_setup, refactoring, tdd_workflow)
- **python3 auto-rewrite**: `bash_manager` reemplaza `python` → `python3` transparentemente
- Elimina 6-9 errores `python: not found` por sesión

## Sprint 16: skills-and-templates (2026-05-26)
- **Tool skills**: `tools/skills/` — 8 YAML enseñando a agentes cuándo/cómo usar cada tool
- **model_override**: agentes pueden anular temperatura vía `model_override` en template YAML
- **Full templates**: `_FULL_TEMPLATE.yaml` para workflows y agentes con todos los campos documentados
- **Decomposer prompt**: análisis/exploración prohibido como primera subtarea
- **Stall detector**: `repeat_tracker` detecta llamadas repetitivas no modificantes como stall

## Sprint 15: real-world-validation (2026-05-26)
- **SAFE_BUILTINS**: `repr`, `type`, `isinstance` agregados al sandbox
- **lsp_manager location=null fix**: `issue.get("location") or {}`
- **6 conversaciones reales**: 6/6 exitosas, 0 crashes, 0 circuit breaker trips
- **Code quality**: 7 archivos correctos y funcionales

## Sprint 14: memory-manager-tests (2026-05-25)
- **MemoryManager**: 36 tests — claves protegidas, read, write, prune, rebuild, perfiles

## Sprint 13: health-check (2026-05-25)
- **Health check CLI**: `core/health.py` — 5 probes (DB, LLM, Redis, Filesystem, Workspace). 11 tests

## Sprint 12: core-coverage (2026-05-25)
- **6 core files**: `utils`, `workflow_state`, `mcp/adapter`, `models`, `mcp/config`, `frustration_detector`. 47 tests

## Sprint 11: fix-hanging-tests (2026-05-25)
- **test_agent_loop.py**: fix de carga de XLMRoberta model. Mockeado `memory_manager` y `CodebaseIndexer`. 5 tests en 2.2s

## Sprint 10: mypy-desktop (2026-05-25)
- **Mypy 0 exclusiones**: `desktop/` (3,404 líneas) pasa mypy con 0 errores. Último `ignore_errors` removido

## Sprint 9: massive-coverage (2026-05-25)
- **10 archivos**: `lru_cache`, `registry`, `token_counter`, `rate_limiter`, `audit`, `parser`, `offline`, `prompts`, `change_tracker`, `specs`. 76 tests

## Sprint 8: production-hardening (2026-05-25)
- **Signal handling**: SIGTERM/SIGINT → graceful shutdown (stop daemons, close loop)
- **Config validation**: `validate_config()` — DATABASE_URL, API keys, ENCRYPTION_KEY
- **Graceful shutdown**: `stop_daemons()` antes de cancelación de tareas

## Sprint 7: agent-experience (2026-05-24)
- **System prompt**: reglas de sandbox code_exec, requerimiento de command en bash_manager
- **Developer profile**: secciones CODE_EXEC, BASH_MANAGER, SHARED CONTEXT
- **Coordinated blackboard**: header instructivo sobre contexto compartido
- **bash_manager spec**: énfasis "OBLIGATORIO" en parámetro command

## Sprint 6: test-coverage (2026-05-24)
- **circuit_breaker.py**: 98% → 100%
- **metrics.py**: 73% → 100%
- **path_resolver.py**: 61% → 100%
- **feature_flags.py**: 53% → 100%

## Sprint 5: orchestration-cleanup-pt2 (2026-05-24)
- **loop.py dedup**: `_execute_tool_calls_and_check_stall()` elimina ~55 líneas duplicadas
- **Circuit breaker in call_stream**: streaming calls tripean el breaker
- **emit_stats in agent loop**: visible al UI, emite start/iteration/end
- **Refactoring template**: `templates/workflows/refactoring.yaml`

## Sprint 4: workflow-health (2026-05-23)
- **WorkflowRunner**: clase con `with_timeout()`, `safe_call()`, `check_cancelled()`, `phase_stats()`
- **Cancellation**: `WorkflowContext.cancelled` + `Session.cancel()`/`is_cancelled`
- **Timeouts**: collaborative (120s/round), coordinated (180s/subtask), TDD (300s/iteration)
- **Deduplication**: `_collect_files_written()`, `tool_matches_allowlist`, umbral de compresión unificado

## Sprint 3: lsp-fix (2026-05-23)
- **lsp_manager ruff_check NoneType**: guard para `[null]` y entradas no-dict. 5 tests nuevos
- **bash_manager python3 -c**: documentada — restricción mantenida por seguridad

## Sprint 2: auditoria-mayo-2026 (2026-05-23)
- **28 commits**: moderator tool filtering, E2E tests, AgentLoopConfig, `_dispatch_route`, métricas por tool
- **Mypy a CI**: pre-commit + CI con baseline exclusion. Circuit breaker. Coordinated DAG tests
- **kairos elimination**: 27 `kairos.get()` migrados a `settings.XXX`
- **Stall detection 3 fixes**: files_written guard, tool_success como progreso, aggregator con file_manager
- **bash_manager guard**: `command=""` previene crashes por omisiones de DeepSeek
- **Export dedup**: filenames estables por conversation ID

## Sprint 1: restructure (2026-05-22)
- **Project restructure**: 33 archivos movidos a `llm/`, `agents/`, `tools/`, `orchestration/`. `features/` eliminado
- **Code deduplication**: `_tool_calls_from_response` en `llm/parser.py`, `_tool_matches_allowlist` en `tools/specs.py`
- **8 commits, 0 regresiones, 338 tests pass**

**Total: 26 sprints (latest 26b), ~698 tests, 0 mypy errors, 0 rollbacks.**
