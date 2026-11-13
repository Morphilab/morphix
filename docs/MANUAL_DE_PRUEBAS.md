# Testing Manual — Morphix / Manual de Pruebas — Morphix

=== ":flag_gb: Exhaustive Manual Testing Guide"

    Covers the **23 tools** (plus the interception-only `ask_clarification`), **5 agents**, the **10 DSL workflow presets**, the **3 execution routes**,
    all **flows/functionality**, the **GUI** (multi-session cockpit), **safety nets**, and the
    **automated suite**.

=== ":flag_es: Guía de pruebas manuales **exhaustiva**"

    Cubre los **23 tools** (más el `ask_clarification` por intercepción), **5 agentes**, los **10 presets DSL de workflow**, las **3 rutas de ejecución**,
    todos los **flujos/funcionalidades**, la **GUI** (cockpit multi-sesión), las **redes de
    seguridad** y la **suite automatizada**.

---

=== ":flag_gb: Test Format"

    > **ID** · *Objective* · **Precondition** · **Steps** · **Prompt/Command** (copy‑paste) · **Data** · **Expected result** · `[ ] OK  [ ] FALLA`

=== ":flag_es: Formato de cada prueba"

    > **ID** · *Objetivo* · **Precondición** · **Pasos** · **Prompt/Comando** (copy‑paste) · **Datos** · **Resultado esperado** · `[ ] OK  [ ] FALLA`

---

=== ":flag_gb: Conventions"

    - 🟢 = base functionality (always available). 🟡 = **optional/conditional** (requires extra config).
    - Where it says *"Chat → agent X"* use the **Agent combo** in the top bar or an **agent card** in Dashboard.
    - Where it says *"Orchestrate → workflow X"* use a **workflow card** in Dashboard (activates Orchestrate mode).

=== ":flag_es: Convenciones"

    - 🟢 = funcionalidad base (siempre disponible). 🟡 = **opcional/condicional** (requiere config extra).
    - Donde dice *“Chat → agente X”* usa el **combo Agente** del top bar o una **card de agente** en Dashboard.
    - Donde dice *“Orquestar → workflow X”* usa una **card de workflow** en Dashboard (activa el modo Orquestar).

---

=== ":flag_gb: Coverage Matrix (quick checklist)"

    **Tools (24):** `[ ]` file_manager `[ ]` bash_manager `[ ]` git_manager `[ ]` test_runner `[ ]` lsp_manager `[ ]` code_exec `[ ]` diff_editor `[ ]` file_view `[ ]` code_search `[ ]` pdf_read `[ ]` vision_analyze `[ ]` memory_inspector `[ ]` project_docs `[ ]` load_skill `[ ]` goal_create `[ ]` goal_get `[ ]` goal_update `[ ]` goal_round `[ ]` todo_write `[ ]` todo_get `[ ]` plan_mode `[ ]` exit_plan_mode `[ ]` delegate_task · ask_clarification (interception)

    **Agents (5):** `[ ]` developer `[ ]` analista `[ ]` conversacional `[ ]` moderador `[ ]` test_author

    **Workflows (10 presets DSL):** `[ ]` development `[ ]` coordinated `[ ]` collaborative `[ ]` tdd `[ ]` bdd `[ ]` sdd `[ ]` edd `[ ]` domain_tdd `[ ]` reflexion `[ ]` long_horizon

    **Routes (3):** `[ ]` direct tool `[ ]` DSL workflow `[ ]` bot canonical chat

    **Features:** `[ ]` clarification `[ ]` continuity `[ ]` project (create/import/pre‑load) `[ ]` export (md/json/pdf/html) `[ ]` history `[ ]` PDF `[ ]` offline🟡 `[ ]` MCP🟡 `[ ]` memory/profile

    **GUI:** `[ ]` Maestro cockpit (multi-sesión) `[ ]` Dashboard `[ ]` History `[ ]` Editor `[ ]` Config `[ ]` Analytics `[ ]` Memoria `[ ]` Bots

    **Security:** `[ ]` undercover `[ ]` bash sanitization `[ ]` sandbox `[ ]` rate limiter🟡 `[ ]` circuit breaker🟡

    **Automated:** `[ ]` pytest `[ ]` ruff `[ ]` black `[ ]` mypy `[ ]` pre‑commit `[ ]` alembic `[ ]` health

=== ":flag_es: Matriz de cobertura (checklist rápido)"

    **Tools (24):** `[ ]` file_manager `[ ]` bash_manager `[ ]` git_manager `[ ]` test_runner `[ ]` lsp_manager `[ ]` code_exec `[ ]` diff_editor `[ ]` file_view `[ ]` code_search `[ ]` pdf_read `[ ]` vision_analyze `[ ]` memory_inspector `[ ]` project_docs `[ ]` load_skill `[ ]` goal_create `[ ]` goal_get `[ ]` goal_update `[ ]` goal_round `[ ]` todo_write `[ ]` todo_get `[ ]` plan_mode `[ ]` exit_plan_mode `[ ]` delegate_task · ask_clarification (intercepción)

    **Agentes (5):** `[ ]` developer `[ ]` analista `[ ]` conversacional `[ ]` moderador `[ ]` test_author

    **Workflows (10 presets DSL):** `[ ]` development `[ ]` coordinated `[ ]` collaborative `[ ]` tdd `[ ]` bdd `[ ]` sdd `[ ]` edd `[ ]` domain_tdd `[ ]` reflexion `[ ]` long_horizon

    **Rutas (3):** `[ ]` tool directa `[ ]` workflow DSL `[ ]` chat canónico de bot

    **Features:** `[ ]` clarification `[ ]` continuidad `[ ]` proyecto (crear/importar/pre‑cargar) `[ ]` export (md/json/pdf/html) `[ ]` history `[ ]` PDF `[ ]` offline🟡 `[ ]` MCP🟡 `[ ]` memoria/perfil

    **GUI:** `[ ]` cockpit Maestro (multi-sesión) `[ ]` Dashboard `[ ]` History `[ ]` Editor `[ ]` Config `[ ]` Analytics `[ ]` Memoria `[ ]` Bots

    **Seguridad:** `[ ]` undercover `[ ]` bash sanitization `[ ]` sandbox `[ ]` rate limiter🟡 `[ ]` circuit breaker🟡

    **Automatizado:** `[ ]` pytest `[ ]` ruff `[ ]` black `[ ]` mypy `[ ]` pre‑commit `[ ]` alembic `[ ]` health

---

=== ":flag_gb: Environment Setup"

    ```bash
    # 1) Dependencies
    uv sync --all-groups

    # 2) Environment variables — copy and edit
    cp example.env .env
    #   Required:
    #     DATABASE_URL=postgresql://user:pass@localhost:5432/morphix
    #     DEEPINFRA_API_KEY=               (at least one API key — default provider)
    #   Optional:
    #     OLLAMA_BASE_URL=http://localhost:11434  OLLAMA_MODEL=gpt-oss:120b-cloud (offline mode 🟡, as shipped in example.env)
    #     UNDERCOVER_MODE=true  DAEMON_MODE=true  ALLOW_CODE_EXECUTION=true

    # 3) Database
    uv run alembic upgrade head

    # 4) Launch the GUI
    uv run python run.py
    ```

=== ":flag_es: Preparación del entorno"

    ```bash
    # 1) Dependencias
    uv sync --all-groups

    # 2) Variables — copia y edita
    cp example.env .env
    #   Obligatorias:
    #     DATABASE_URL=postgresql://user:pass@localhost:5432/morphix
    #     DEEPINFRA_API_KEY=               (al menos una API key — proveedor predeterminado)
    #   Opcionales:
    #     OLLAMA_BASE_URL=http://localhost:11434  OLLAMA_MODEL=gpt-oss:120b-cloud (modo offline 🟡, como viene en example.env)
    #     UNDERCOVER_MODE=true  DAEMON_MODE=true  ALLOW_CODE_EXECUTION=true

    # 3) Base de datos
    uv run alembic upgrade head

    # 4) Lanzar la GUI
    uv run python run.py
    ```

---

=== ":flag_gb: Health Check (CLI)"

    ```bash
    uv run python -c "import asyncio; from core.health import run_health_check; r = asyncio.run(run_health_check()); print(r.format())"
    ```
    > Expected: rows for **Database, LLM, Memory Dir, Templates, Workspace, Embeddings** (all should report OK when PostgreSQL is up; Embeddings may show a neutral "lazy" status until first use).

    **Login:** the GUI opens a `LoginDialog`. Use the configured password (`PASSWORD_HASH`) or, in dev without a hash, follow the dialog instructions.

=== ":flag_es: Health check (CLI)"

    ```bash
    uv run python -c "import asyncio; from core.health import run_health_check; r = asyncio.run(run_health_check()); print(r.format())"
    ```
    > Esperado: filas **Database, LLM, Memory Dir, Templates, Workspace, Embeddings** (todo OK con PostgreSQL levantado; Embeddings puede mostrar un estado neutro "lazy" hasta el primer uso).

    **Login:** la GUI abre un `LoginDialog`. Usa la contraseña configurada (`PASSWORD_HASH`) o, en dev sin hash, sigue las instrucciones del diálogo.

---

=== ":flag_gb: Lab Project (use for nearly all tests)"

    1. **Dashboard** tab → in **Proyectos** click **＋ Nuevo** → type `test_lab` → OK.
    2. The **Proyecto** combo (Maestro) shows `test_lab`, and the window status bar displays *"✅ Proyecto 'test_lab' creado y activado."* (the message is Spanish by code).
    3. Create the sample files from **Appendix A** (ask the `developer` agent to create them, or use direct `file_manager` commands).

    > Note about **direct commands** (`tool: action, key=value`): the first token after `:` is the *action* (a single word), and parameters go as `key=value` separated by commas. **Values cannot contain commas** (breaks parsing) or line breaks — for complex content, use the agent. The command validates that the tool exists in the registry.

=== ":flag_es: Proyecto de laboratorio (úsalo en casi todas las pruebas)"

    1. Pestaña **Dashboard** → en **Proyectos** pulsa **＋ Nuevo** → escribe `test_lab` → Aceptar.
    2. El combo **Proyecto** (Maestro) muestra `test_lab` y la barra de estado de la ventana muestra *“✅ Proyecto 'test_lab' creado y activado.”*
    3. Crea los archivos de muestra del **Apéndice A** (pídele al agente `developer` que los cree, o usa comandos directos `file_manager`).

    > Nota sobre **comandos directos** (`tool: action, clave=valor`): el primer token tras `:` es la *acción* (una sola palabra), y los parámetros van como `clave=valor` separados por comas. **Los valores no pueden contener comas** (rompen el parseo) ni saltos de línea — para contenido complejo, usa el agente. El comando valida que el tool exista en el registro.

---

## §0 — Smoke / Health / Salud

| ID | Objective / Objetivo | Steps / Command / Pasos / Comando | Expected / Esperado |
|----|----------------------|-----------------------------------|---------------------|
| S0.1 | System health / Salud del sistema | Run the CLI health check (above) / Ejecuta el health check CLI (arriba) | 6 rows; Database/LLM/Workspace = OK / 6 filas; Database/LLM/Workspace = OK |
| S0.2 | GUI startup / Arranque GUI | `uv run python run.py` | Opens login → window with sidebar tabs Dashboard/Maestro/Historial/Editor/Config/Analytics/Memoria/Bots / Abre login → ventana con tabs Dashboard/Maestro/Historial/Editor/Config/Analytics/Memoria/Bots |
| S0.3 | Online/Offline indicator / Indicador Online/Offline | Dashboard (mode icon) / Dashboard (icono de modo) | Shows **Online** (green) if `OFFLINE_MODE=false` / Muestra **Online** (verde) si `OFFLINE_MODE=false` |
| S0.4 | Toggle Offline / Toggle Offline | Click **Activar Offline** (Dashboard footer) / Pulsa **Activar Offline** (pie del Dashboard) | Changes to **Offline** (amber) and button to *Desactivar Offline* / Cambia a **Offline** (ámbar) y el botón a *Desactivar Offline* |
| S0.5 | Create project / Crear proyecto | Dashboard → **＋ Nuevo** → `test_lab` / Dashboard → **＋ Nuevo** → `test_lab` | Proyecto combo = test_lab; status bar message / Combo Proyecto = test_lab; mensaje en la barra de estado |

---

## §1 — Tools (23)

> For tools with `project_root`, **select `test_lab`** first and test via **Chat → developer** (the agent receives `project_root` from context). **Direct commands** are included as quick smoke tests.
>
> The 12 cases below cover the classic toolset. The remaining registered tools are best exercised through the agent in Chat mode: `file_view` ("open app.py in the viewer"), `memory_inspector` ("list my stored memories"), `project_docs` ("search the project docs for conventions"), `vision_analyze` 🟡 ("describe the image chart.png" — needs the vision role), `load_skill` (implicit when a skill matches), the `goal_*`/`todo_*` family ("create a goal to ship v1" / "write a todo list for this refactor"), and `plan_mode`/`exit_plan_mode` ("plan the refactor before coding" with analista).

### T1.1 — file_manager 🟢
- *Objective / Objetivo:* create/read/append/delete files / crear/leer/añadir/borrar archivos.
- **Chat → developer**, project `test_lab`:
  ```
  Create a file saludo.py with a function hola() that prints "Hola Morphix", then read it and show me its contents.
  ```
- **Direct (smoke):**
  ```
  file_manager: write, path=saludo.py, content=print('Hola')
  file_manager: read, path=saludo.py
  file_manager: append, path=saludo.py, content=# fin
  file_manager: delete, path=saludo.py
  ```
- *Expected / Esperado:* the file appears in `memory/main/code_projects/test_lab/`; `read` returns the content; `delete` removes it. **Verify on disk.** / el archivo aparece en `memory/main/code_projects/test_lab/`; `read` devuelve el contenido; `delete` lo elimina. **Verifica en disco.**

### T1.2 — bash_manager 🟢
- **Chat → developer**, `test_lab`:
  ```
  Run the command "ls -la" and then "python --version".
  ```
- **Direct:** `bash_manager: run, command=ls -la`
- *Expected / Esperado:* shell output in the **Bash** tab (Detail). `python` automatically rewrites to `python3`. / salida del shell en el tab **Bash** (Detalle). `python` se reescribe a `python3` automáticamente.

### T1.3 — git_manager 🟢
- **Chat → developer**, `test_lab`:
  ```
  Initialize a git repository in this project, add all files, and commit with message "init test_lab". Then show me the log.
  ```
- **Direct:** `git_manager: init, project_root=code_projects/test_lab`
- *Expected / Esperado:* `init`→repo created; `commit`→hash; `log`→shows commit. (Messages starting with `❌` are rejected by design.) / `init`→repo creado; `commit`→hash; `log`→muestra el commit. (Mensajes que empiezan con `❌` son rechazados por diseño.)

### T1.4 — test_runner 🟢
- **Precondition / Precondición:** create `test_app.py` (Appendix A / Apéndice A) in `test_lab`.
- **Chat → developer:**
  ```
  Run the tests in the file test_app.py of this project and tell me how many pass.
  ```
- *Expected / Esperado:* parses pytest counts (passed/failed), doesn't rely solely on returncode. / parsea conteos de pytest (pasados/fallidos), no depende solo de returncode.

### T1.5 — lsp_manager 🟢
- **Precondition / Precondición:** `app.py` (Appendix A / Apéndice A) in `test_lab`.
- **Chat → developer / analista:**
  ```
  Run ruff_check on app.py and tell me what lint issues it finds.
  ```
- **Direct:** `lsp_manager: ruff_check, file=app.py, project_root=code_projects/test_lab`
- *Expected / Esperado:* list of ruff diagnostics (or "no issues"). Also test `diagnostics` and `definition`. / lista de diagnósticos de ruff (o “sin problemas”). Prueba también `diagnostics` y `definition`.

### T1.6 — code_exec 🟢
- **Chat → developer:**
  ```
  Use code_exec to calculate the mean and standard deviation of [3, 7, 7, 19, 24] with numpy.
  ```
- *Expected / Esperado:* numeric result. **Sandbox:** see S7.3 (must block `import os`). / resultado numérico. **Sandbox:** ver S7.3 (debe bloquear `import os`).

### T1.7 — diff_editor 🟢
- **Precondition / Precondición:** `app.py` in `test_lab`.
- **Chat → developer:**
  ```
  Apply a surgical change to app.py: rename the function "sumar" to "suma" using diff_editor (action apply). Don't rewrite the entire file.
  ```
- *Expected / Esperado:* the diff is applied; `app.py` changes only that line. Accepts `path`/`content` aliases. / el diff se aplica; `app.py` cambia solo esa línea. Acepta alias `path`/`content`.

### T1.8 — code_search 🟢
- **Chat → analista**, `test_lab`:
  ```
  Search for the pattern "def " in the .py files of the project and list where it appears.
  ```
- **Direct:** `code_search: buscar, pattern=def , include=*.py`
- *Expected / Esperado:* matches with file:line. / coincidencias con archivo:línea.

### T1.9 — pdf_read 🟢
- **Precondition / Precondición:** copy any PDF to `memory/main/code_projects/test_lab/doc.pdf`.
- **Direct:** `pdf_read: leer, path=test_lab/doc.pdf`
- *Expected / Esperado:* extracted text (pdfplumber). / texto extraído (pdfplumber).

### T1.10 — ask_clarification 🟢 (interception)
- **Chat → developer** (or **Orchestrate → development**), `test_lab`, deliberately ambiguous prompt:
  ```
  Create a user endpoint.
  ```
- *Expected / Esperado:* the agent **may** pause and ask (e.g. "Which framework / what fields?"). In Maestro, **"⏸️ Paused: …"** appears, the input placeholder changes, and answering **resumes** the workflow. *(LLM‑dependent; retry with more ambiguous prompts if it doesn't pause — see F5.1.)* / el agente **puede** pausar y preguntar (p.ej. “¿Qué framework / qué campos?”). En Maestro aparece **“⏸️ Pausa: …”**, el placeholder del input cambia, y al responder el workflow **reanuda**. *(Depende del LLM; reintenta con prompts más ambiguos si no pausa — ver F5.1.)*

---

## §2 — Agents / Agentes (5)

> Select them via **card in Dashboard** or via the **Agent combo** in Maestro (Chat mode). / Selecciónalos por **card en Dashboard** o por el **combo Agente** en Maestro (modo Chat).

| ID | Agent / Agente | Prompt (copy‑paste) | Expected / Esperado |
|----|----------------|---------------------|---------------------|
| A2.1 | **developer** | `Create a script fibonacci.py that prints the first 10 Fibonacci numbers and run it.` | Writes the file + executes it (file_manager + bash/code_exec). / Escribe el archivo + lo ejecuta (file_manager + bash/code_exec). |
| A2.2 | **analista** | `Analyze app.py: explain what it does, what patterns it uses, and 3 risks. Do NOT modify anything.` | Only reads/analyzes; **does not** write files. / Solo lee/analiza; **no** escribe archivos. |
| A2.3 | **conversacional** | `Explain what a decorator is in Python with a simple example.` | Conversational response (no tools). / Respuesta conversacional (sin tools). |
| A2.4 | **moderador** | (exercised in the **collaborative** workflow, W3.3) / (se ejercita en el workflow **collaborative**, W3.3) | Produces panel consensus. / Produce consenso del panel. |
| A2.5 | **test_author** | `Write the acceptance tests for a function that validates emails, before implementing it.` | Writes test files only; does not implement or weaken tests. / Solo escribe tests; no implementa ni debilita tests. |

---

## §3 — Workflows (10 presets DSL)

> Orchestrate **requires a selected project** (except `collaborative`). Observe the **Execution** panel (Progress, Subtasks ✅🔵❌⏳, Created Files) and the **Detail** tab (Agents / Diagram / Log / Bash).

### W3.1 — development 🟢
- **Steps / Pasos:** Dashboard → **development** card (enters Maestro/Orchestrate). Project `test_lab`.
- **Prompt:**
  ```
  Create a TODO console app in Python with commands add, list, and complete, storing in a JSON file. Include a pytest test.
  ```
- *Expected / Esperado:* decompose → multiple subtasks → execution → aggregation. Subtasks complete; files appear in **Created files**. / decompose → varias subtareas → ejecución → agregación. Subtareas se completan; archivos aparecen en **Archivos creados**.

### W3.2 — coordinated 🟢
- **Steps / Pasos:** Dashboard → **coordinated** card. Project `test_lab`.
- **Prompt:**
  ```
  Create a user REST API with: 1) model + schema, 2) CRUD endpoints, 3) tests. Do it by phases.
  ```
- *Expected / Esperado:* decompose → subtasks executed in a **dynamic parallel loop** (`loop.parallel`, up to 5 concurrent via `parallel_max: 5`); the **Diagram** tab tracks per-subtask status; results are verified and aggregated at the end. / descomposición → subtareas ejecutadas en un **loop paralelo dinámico** (`loop.parallel`, hasta 5 concurrentes con `parallel_max: 5`); el tab **Diagrama** sigue el estado por subtarea; al final se verifica y agrega.

### W3.3 — collaborative 🟢 (no project required / no requiere proyecto)
- **Steps / Pasos:** Dashboard → **collaborative** card.
- **Prompt:**
  ```
  Debate: PostgreSQL or MongoDB for a user profile and sessions microservice? Analyze pros/cons and recommend one.
  ```
- *Expected / Esperado:* panel debate (3 rounds) among agents + **moderador** synthesizing consensus. Per-agent responses in **Agents** tab. / panel debate (3 rondas) entre agentes + **moderador** que sintetiza consenso. Respuestas por agente en tab **Agentes**.

### W3.4 — tdd 🟢
- **Steps / Pasos:** Dashboard → **tdd** card (enters Maestro/Orchestrate). Project `test_lab`.
- **Prompt:**
  ```
  Implement an is_prime(n) function with TDD: first the tests, then the implementation until they pass.
  ```
- *Expected / Esperado:* loop (max 5 iterations) writes tests → runs them via `test_runner` → fixes → repeats until **all tests pass** (deterministic exit: parsed pytest counts, not LLM judgment). Status **Iteración N/5** in Ejecución. / bucle (máx. 5 iteraciones) escribe tests → los ejecuta con `test_runner` → corrige → repite hasta que **todos pasen** (salida determinista: conteos parseados de pytest, no juicio del LLM). Estado **Iteración N/5** en Ejecución.

### W3.5 — bdd / sdd / edd / domain_tdd / reflexion / long_horizon 🟢 (same mechanics / misma mecánica)
- **Steps / Pasos:** Dashboard → preset card. Project `test_lab` (all five require it).
- **Prompts (one per preset):**
  ```
  bdd:        As a user I want to reset my password with a token that expires in 1h.
  sdd:        Build a CSV importer with column validation and a dry-run mode.
  edd:        Implement a rate limiter with at least 5 passing test cases.
  domain_tdd: Model a library loan system with reservations and fines.
  reflexion:  Write a function that merges two sorted lists, and polish it.
  long_horizon: Build a small library with spec, tests and implementation (long run).
  ```
- *Expected / Esperado:* each preset runs its declared steps (e.g. bdd: Gherkin stories → failing test per story → minimal implementation → green loop; reflexion: generator → critic → loop until "APROBADO" or 3 iterations). Human **gates** pause the run with a clarifying question; answering resumes it. / cada preset corre sus pasos declarados (p.ej. bdd: historias Gherkin → test fallido por historia → implementación mínima → bucle a verde; reflexion: generador → crítico → bucle hasta "APROBADO" o 3 iteraciones). Los **gates** humanos pausan la ejecución con una pregunta; al responder, se reanuda.

---

## §4 — Execution Routes / Rutas de ejecución

`run_full_workflow` dispatches to exactly three routes / `run_full_workflow` despacha exactamente a tres rutas:

| ID | Route / Ruta | Trigger / Disparador | Expected / Esperado |
|----|-------------|---------------------|---------------------|
| R4.1 | Direct tool / Tool directa | `file_manager: read, path=app.py` (with `test_lab`) | Executes the tool without orchestration; "Completado (tool directa)". / Ejecuta el tool sin orquestación; “Completado (tool directa)”. |
| R4.2 | DSL workflow / Workflow DSL | **Orchestrate** mode → any non-command task (W3.1) | The active preset compiles → validates → the engine runs its steps (decompose → execute → aggregate). / El preset activo compila → valida → el motor ejecuta sus pasos (descomponer → ejecutar → agregar). |
| R4.3 | Bot canonical chat / Chat canónico de bot | **Bots** tab → open a bot's chat → send a message | The bot answers from its eternal conversation (`bots_runner`), with its own identity/tools — no workflow templates involved. / El bot responde desde su conversación eterna (`bots_runner`), con su identidad/tools — sin plantillas de workflow. |

---

## §5 — Features / Flows / Flujos

### F5.1 — Clarification (pause / resume) 🟢
- **Chat/Orchestrate**, ambiguous prompt (see T1.10). If it pauses: answer the question in the input.
- *Expected / Esperado:* `PausedSession` persists (survives restart); on answering, the workflow continues from the pause point. / `PausedSession` persiste (sobrevive reinicio); al responder, el workflow continúa desde el punto de pausa.

### F5.2 — Conversation Continuity / Continuidad de conversación 🟢
- After a response, **without clicking "New conversation"**, send a follow‑up:
  ```
  Now add input validation to the previous thing.
  ```
- *Expected / Esperado:* the system uses previous context (`is_follow_up` flag); does not recreate from scratch. / el sistema usa el contexto previo (flag `is_follow_up`); no recrea desde cero.

### F5.3 — Project: create / import / pre‑load 🟢
- **Create / Crear:** Dashboard → **＋ Nuevo** (already done in S0.5).
- **Import / Importar:** Dashboard → **⌂ Importar** → select a folder with code → copies to `code_projects/<name>`.
- **Pre‑load / Pre‑cargar:** select the project → **Maestro** top bar → project menu → **⟳ Pre-cargar índice** → progress → *"✅ N chunks en FAISS"*.

### F5.4 — Export (md / json / pdf / html) 🟢
- In Maestro, after a conversation: **Download** in each combo format (md, json, pdf, html).
- *Expected / Esperado:* file in `exports/`; HTML uses highlighting (pygments if available, fallback if not); no watermarks; includes real project files. / archivo en `exports/`; el HTML usa resaltado (pygments si está disponible, fallback si no); sin watermarks; incluye archivos reales del proyecto.

### F5.5 — History 🟢
- **History** tab → **Refresh** → select a conversation → detail is shown.
- **Continue:** button **Continue** → loads the conversation in Maestro for follow‑up.
- **Export:** combo (md/json/pdf) → **Export**. **Delete:** button **Delete**.
- *Esperado (same for both):* / Pestaña **Historial** → **Refrescar** → selecciona una conversación → se muestra el detalle. **Continuar:** botón **Continuar** → carga la conversación en Maestro para seguir. **Exportar:** combo (md/json/pdf) → **Exportar**. **Eliminar:** botón **Eliminar**.

### F5.6 — PDF 🟢
- See T1.9. / Ver T1.9.

### F5.7 — Offline / Ollama 🟡 (requires Ollama running + `ollama pull gpt-oss:120b-cloud`, the model shipped in example.env)
- Click **Activar Offline** (Dashboard) → send `Summarize in 2 lines what Python is.`
- *Expected / Esperado:* responds using the local model; **Offline** indicator. / responde usando el modelo local; indicador **Offline**.

### F5.8 — MCP server 🟡
- In another terminal: `uv run python -m core.mcp.server`
- Connect an MCP client (or send a `tools/list` via stdio JSON‑RPC).
- *Expected / Esperado:* exposes the **22** function‑calling tools (from `TOOL_DEFINITIONS`; `ask_clarification` is not exposed via MCP). / expone las **22** tools function‑calling (de `TOOL_DEFINITIONS`; `ask_clarification` no se expone por MCP).

### F5.9 — Memory / Profile (autoDream) 🟢
- **Chat → conversacional:** `Remember that my favorite language is Rust and I work in GMT-3 timezone.`
- Later (different conversation): `What is my favorite language?`
- *Expected / Esperado:* retrieves the fact from the profile (personal fact extraction + FAISS memory; consolidation every `SELF_HEAL_INTERVAL`s). / recupera el dato del perfil (extracción de hechos personales + memoria FAISS; consolidación cada `SELF_HEAL_INTERVAL`s).

---

## §6 — GUI / Cockpit

| ID | Objective / Objetivo | Steps / Pasos | Expected / Esperado |
|----|----------------------|---------------|---------------------|
| G6.1 | Layout / Layout | Open Maestro / Abre Maestro | 2 columns: **Chat (izq) \| Actividad (der)** — Ejecución/Subtareas/Archivos (colapsables) + tabs Diagrama/Log/Bash; divisores arrastrables, doble clic colapsa columnas. / 2 columnas: **Chat (izq) \| Actividad (der)** — Ejecución/Subtareas/Archivos (colapsables) + tabs Diagrama/Log/Bash; divisores arrastrables, doble clic colapsa columnas. |
| G6.2 | Detail tabs / Tabs Detalle | Click Diagrama / Log / Bash / Click en Diagrama / Log / Bash | Switch without rearranging the layout. / Cambian sin reorganizar el layout. |
| G6.3 | Streaming responsiveness / Responsividad en streaming | Launch a long response (W3.1) / Lanza una respuesta larga (W3.1) | Chat flows without "freezing"; Log doesn't flicker or fully rebuild. / El chat fluye sin “congelarse”; el Log no parpadea ni se reconstruye entero. |
| G6.4 | Chat/Orchestrate mode / Modo Chat/Orquestar | Toggle top bar buttons / Alterna los botones del top bar | Behavior changes; layout does **not** change. / Cambia el comportamiento; **no** cambia el layout. |
| G6.5 | Agent combo / Combo Agente | Select agents in the combo / Selecciona agentes en el combo | Tooltip shows the profile; in Chat fixes the agent. / Tooltip muestra el perfil; en Chat fija el agente. |
| G6.6 | Dashboard / Dashboard | Dashboard tab / Pestaña Dashboard | Workflow and Agent cards (dynamic); click navigates to Maestro. / Cards de Workflows y Agentes (dinámicas); click navega a Maestro. |
| G6.7 | Config / Config | Config tab / Pestaña Config | 5 sub‑tabs: **Models / Tools / System / Appearance / Skills**. / 5 sub‑tabs: **Modelos / Herramientas / Sistema / Apariencia / Skills**. |
| G6.8 | Analytics / Analytics | Analytics tab / Pestaña Analytics | Metrics/usage displayed. / Métricas/uso se muestran. |
| G6.9 | Stop per-run / ⏹ por sesión | Launch a workflow (W3.1) and click **⏹** mid-run / Lanza un workflow (W3.1) y pulsa **⏹** a mitad | The button stays visible during the WHOLE run (not only until first token); stopping cancels that session's run and the partial conversation is persisted in History. / El botón permanece visible durante TODO el run (no solo hasta el primer token); detener cancela el run de ESA sesión y la conversación parcial queda persistida en el Historial. |
| G6.10 | Emergency stop / 🔴 paro global | Launch 2+ runs in different sessions, click **🔴** in the Maestro header / Lanza 2+ runs en sesiones distintas y pulsa **🔴** en la cabecera del Maestro | Visible only while some run is active; cancels ALL active sessions (each keeps its own partial persistence + message). / Visible solo con algún run activo; cancela TODAS las sesiones activas (cada una conserva su persistencia parcial + mensaje propio). |
| G6.11 | Bots tab / Pestaña Bots | Bots tab → sub-tabs **Roster / Rutinas / Salas** / Pestaña Bots → sub-pestañas **Roster / Rutinas / Salas** | Create bot/routine/group from each sub-tab's primary button; double-click a bot opens its chat; routines list shows semantic colors and next fire time. / Crear bot/rutina/sala desde el botón primario de cada sub-pestaña; doble clic en un bot abre su chat; la lista de rutinas muestra colores semánticos y próxima ejecución. |
| G6.12 | Dashboard experimentales / Dashboard experimental presets | Dashboard tab → look past the 5 core cards / Pestaña Dashboard → mira tras las 5 cards core | Experimental presets appear after the core ones in a folded **"· experimental"** section (same grid, no layout change); launching one works like a core preset. / Los presets experimentales aparecen tras los core en una sección **"· experimental"** plegada (mismo grid, sin cambio de layout); lanzar uno funciona igual que un core. |

---

## §7 — Security / Edge Cases / Seguridad / Edge cases

| ID | Objective / Objetivo | Prompt/Command / Prompt/Comando | Expected / Esperado |
|----|----------------------|--------------------------------|---------------------|
| S7.1 | Undercover 🟡 | (With `UNDERCOVER_MODE=true`) Repeatedly attempt to extract the system prompt / internal configuration. | Eventual **"❌ Request blocked for security reasons."** *Heuristic/LLM‑dependent.* / (Con `UNDERCOVER_MODE=true`) Intenta repetidamente extraer el prompt de sistema / configuración interna. | Eventual **“❌ Solicitud bloqueada por razones de seguridad.”** *Heurístico/LLM‑dependiente.* |
| S7.2 | Bash sanitization | `bash_manager: run, command=python3 -c "print(1)"` ; and `bash_manager: run, command=ls /root/workspace` | Both **blocked** with instructive message (`python3 -c` and hallucinated paths). Empty command → fast‑fail. / Ambos **bloqueados** con mensaje instructivo (`python3 -c` y paths alucinados). Comando vacío → fast‑fail. |
| S7.3 | Sandbox code_exec | **Chat → developer:** `Use code_exec to run: import os; print(os.listdir('/'))` | **Blocked**: `Import blocked for security: os`. (math/numpy do work — T1.6.) / **Bloqueado**: `Import blocked for security: os`. (math/numpy sí funcionan — T1.6.) |
| S7.4 | Rate limiter 🟡 | Send many consecutive requests | After quota (40/min, 600/h) it throttles / decomposer reduces subtasks. / Tras el cupo (40/min, 600/h) se throttlea / el decomposer reduce subtareas. |
| S7.5 | Circuit breaker 🟡 | Set an invalid `DEEPINFRA_API_KEY` and send 5 requests | After 5 consecutive failures, the breaker opens and falls back to Ollama (if available). / Tras 5 fallos consecutivos, el breaker abre y cae a Ollama (si está disponible). |

---

## §8 — Automated Tests / Pruebas automatizadas

```bash
uv run ruff check .                                   # lint  → 0 issues
uv run black --check .                                # format → no changes
uv run mypy core/ llm/ agents/ tools/ orchestration/ desktop/   # types → 0 errors
uv run pytest                                         # suite → ~2,921 pass (2,752 test functions in 296 modules, measured 2026-11-13)
uv run pre-commit run --all-files                     # all hooks
uv run alembic upgrade head                           # migrations
```

=== ":flag_gb:"

    The PostgreSQL e2e tests (`*_pg`) only run when `DATABASE_URL` is exported — pytest does **not** load `.env`:
    ```bash
    export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
    ```

=== ":flag_es:"

    Los tests e2e de PostgreSQL (`*_pg`) solo corren si `DATABASE_URL` está exportado — pytest **no** carga `.env`:
    ```bash
    export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
    ```

---

## Appendix A / Apéndice A — Sample Data / Datos de muestra (copy‑paste)

> Ask the `developer` agent to create each file with this content, or create them in
> `memory/main/code_projects/test_lab/`. / Pídele al agente `developer` que cree cada archivo con este contenido, o créalos en
> `memory/main/code_projects/test_lab/`.

**`app.py`**
```python
def sumar(a, b):
    return a + b


def restar(a, b):
    return a - b


def main():
    print("suma:", sumar(2, 3))
    print("resta:", restar(5, 2))


if __name__ == "__main__":
    main()
```

**`test_app.py`**
```python
from app import restar, sumar


def test_sumar():
    assert sumar(2, 3) == 5


def test_restar():
    assert restar(5, 2) == 3
```

**`data.csv`**
```csv
nombre,edad,ciudad
Ana,30,Lima
Beto,25,Bogota
Caro,41,Quito
```

=== ":flag_gb: Example Diff (for T1.7 / diff_editor `apply`)"

    ```diff
    --- a/app.py
    +++ b/app.py
    @@
    -def sumar(a, b):
    +def suma(a, b):
         return a + b
    ```

=== ":flag_es: Diff de ejemplo (para T1.7 / diff_editor `apply`)"

    ```diff
    --- a/app.py
    +++ b/app.py
    @@
    -def sumar(a, b):
    +def suma(a, b):
         return a + b
    ```

---

=== ":flag_gb: Reusable Long Prompts"

    ```
    [Analysis] Review the architecture of this project: patterns used, coupling, and 3 prioritized improvements. Do not modify code.

    [Creation] Build a Python CLI script that reads data.csv and generates a report (row count, columns, and basic statistics per numeric column). Include tests.

    [Debate] Evaluate monolith vs micro‑frontends for a 3‑dev team, considering deployment and maintenance; recommend one.
    ```

=== ":flag_es: Prompts largos reutilizables"

    ```
    [Análisis] Revisa la arquitectura de este proyecto: patrones usados, acoplamiento, y 3 mejoras priorizadas. No modifiques código.

    [Creación] Construye un script CLI en Python que lea data.csv y genere un reporte (nº de filas, columnas y estadísticas básicas por columna numérica). Incluye tests.

    [Debate] Evalúen monolito vs micro‑frontends para un equipo de 3 devs, considerando despliegue y mantenimiento; recomienden uno.
    ```

---

## Appendix B / Apéndice B — Results Checklist / Checklist de resultados

| Area / Área | Case / Caso | OK | Falla | Notes / Notas |
|------|------|:--:|:-----:|-------|
| Health / Salud | S0.1–S0.5 | ☐ | ☐ | |
| Tools | T1.1 file_manager | ☐ | ☐ | |
| Tools | T1.2 bash_manager | ☐ | ☐ | |
| Tools | T1.3 git_manager | ☐ | ☐ | |
| Tools | T1.4 test_runner | ☐ | ☐ | |
| Tools | T1.5 lsp_manager | ☐ | ☐ | |
| Tools | T1.6 code_exec | ☐ | ☐ | |
| Tools | T1.7 diff_editor | ☐ | ☐ | |
| Tools | T1.8 code_search | ☐ | ☐ | |
| Tools | T1.9 pdf_read | ☐ | ☐ | |
| Tools | T1.10 ask_clarification | ☐ | ☐ | |
| Agents / Agentes | A2.1–A2.5 | ☐ | ☐ | |
| Workflows | W3.1 development | ☐ | ☐ | |
| Workflows | W3.2 coordinated | ☐ | ☐ | |
| Workflows | W3.3 collaborative | ☐ | ☐ | |
| Workflows | W3.4 tdd | ☐ | ☐ | |
| Workflows | W3.5 bdd/sdd/edd/domain_tdd/reflexion/long_horizon | ☐ | ☐ | |

| Routes / Rutas | R4.1–R4.3 | ☐ | ☐ | |
| Features | F5.1–F5.9 | ☐ | ☐ | |
| GUI | G6.1–G6.8 | ☐ | ☐ | |
| Security / Seguridad | S7.1–S7.5 | ☐ | ☐ | |
| Automated / Automatizado | §8 | ☐ | ☐ | |
