# Getting Started

=== ":flag_gb: English"

    ## Prerequisites

    - **Python 3.12** (required; `<3.14`)
    - **PostgreSQL** — install and create a database
    - **Poetry** — [install via pipx](https://python-poetry.org/docs/#installation)
    - **Redis** (optional, for caching)
    - **Ollama** (optional, for offline mode)

    ## Install

    ```bash
    git clone https://github.com/morphilab/morphix.git
    cd morphix
    poetry install --with dev
    ```

    ## Configure

    ```bash
    cp example.env .env
    ```

    Edit `.env` and fill in the required values:

    ```ini
    # Required: PostgreSQL connection
    DATABASE_URL=postgresql://postgres:your_password@localhost:5432/morphix

    # Required: at least one API key
    DEEPSEEK_API_KEY=sk-xxxxxxxxxxx

    # Optional: for offline/local mode
    # OLLAMA_BASE_URL=http://localhost:11434
    # OFFLINE_MODE=true
    ```

    ## Database Setup

    ```bash
    # Create the database
    createdb morphix

    # Run migrations
    poetry run alembic upgrade head
    ```

    ## Launch

    ```bash
    poetry run python run.py
    ```

    The GUI window opens. On first launch, the `main` workspace is created automatically.

    ## First Steps

    1. **Dashboard** — The home screen shows available workflows. Pick one (e.g., "Development").
    2. **Select or create a project** — Click the project selector in the top bar. Create a new project or select an existing one.
    3. **Maestro tab** — This is where you interact with Morphix. Type a task in the input field:
        ```
        Create a Flask API with a /health endpoint
        ```
    4. **Watch the workflow** — Morphix analyzes your task, decomposes it into subtasks, routes them to agents, executes tools, and aggregates the results.
    5. **Review and edit** — Use the Editor tab to view and modify generated files. The History tab stores all past conversations.

    ## Available Workflows

    | Workflow | Best for |
    |----------|----------|
    | **Development** | General coding tasks — create, modify, refactor |
    | **Coordinated** | Multi-agent DAG — design → implement → verify phases |
    | **Collaborative** | Debate-style — multiple agents review and improve |
    | **TDD** | Test-driven development — write tests, implement, verify |

    ## Direct Tool Commands

    You can call tools directly from the chat input using the format `tool_name: action, key=value`:

    | Command | What it does |
    |---------|-------------|
    | `file_manager: read, path=src/main.py` | Read a file |
    | `file_manager: write, path=app.py, content=print('hi')` | Write a file |
    | `bash_manager: command=pytest tests/` | Run a shell command |
    | `git_manager: log, project_root=myproject` | View git log |
    | `code_search: pattern=def foo` | Search codebase |

    ## Common Issues

    ### "Module not found" errors

    Run commands via Poetry: `poetry run python script.py` or `poetry run pytest`.

    ### Database connection refused

    Check that PostgreSQL is running: `pg_isready`. Verify `DATABASE_URL` in `.env`.

    ### "No API key configured"

    Set at least one of: `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` in `.env`. For offline mode, set `OFFLINE_MODE=true` and ensure Ollama is running.

    ### Tools not executing

    Check that the tool is in the workflow's `tools.allowed` list. For direct tool commands, the tool must be globally registered.

    ### Workspace not found

    Workspaces are created on first use. Switch to a workspace via the Dashboard or config. The default is `main`.

=== ":flag_es: Español"

    ## Requisitos previos

    - **Python 3.12** (obligatorio; `<3.14`)
    - **PostgreSQL** — instalar y crear una base de datos
    - **Poetry** — [instalar vía pipx](https://python-poetry.org/docs/#installation)
    - **Redis** (opcional, para caché)
    - **Ollama** (opcional, para modo offline)

    ## Instalación

    ```bash
    git clone https://github.com/morphilab/morphix.git
    cd morphix
    poetry install --with dev
    ```

    ## Configuración

    ```bash
    cp example.env .env
    ```

    Edita `.env` y completa los valores requeridos:

    ```ini
    # Obligatorio: conexión a PostgreSQL
    DATABASE_URL=postgresql://postgres:tu_contraseña@localhost:5432/morphix

    # Obligatorio: al menos una clave de API
    DEEPSEEK_API_KEY=sk-xxxxxxxxxxx

    # Opcional: para modo offline/local
    # OLLAMA_BASE_URL=http://localhost:11434
    # OFFLINE_MODE=true
    ```

    ## Configuración de la base de datos

    ```bash
    # Crear la base de datos
    createdb morphix

    # Ejecutar migraciones
    poetry run alembic upgrade head
    ```

    ## Iniciar

    ```bash
    poetry run python run.py
    ```

    La ventana de la GUI se abre. En el primer inicio, el workspace `main` se crea automáticamente.

    ## Primeros pasos

    1. **Dashboard** — La pantalla principal muestra los workflows disponibles. Elige uno (ej. "Development").
    2. **Selecciona o crea un proyecto** — Usa el selector de proyectos en la barra superior. Crea un proyecto nuevo o selecciona uno existente.
    3. **Pestaña Maestro** — Aquí interactúas con Morphix. Escribe una tarea en el campo de entrada:
        ```
        Crea una API Flask con un endpoint /health
        ```
    4. **Observa el workflow** — Morphix analiza tu tarea, la descompone en subtareas, las asigna a agentes, ejecuta herramientas y agrega los resultados.
    5. **Revisa y edita** — Usa la pestaña Editor para ver y modificar archivos generados. La pestaña Historial guarda todas las conversaciones pasadas.

    ## Workflows disponibles

    | Workflow | Ideal para |
    |----------|------------|
    | **Development** | Tareas generales de código — crear, modificar, refactorizar |
    | **Coordinated** | DAG multi-agente — fases de diseño → implementación → verificación |
    | **Collaborative** | Estilo debate — múltiples agentes revisan y mejoran |
    | **TDD** | Desarrollo guiado por tests — escribir tests, implementar, verificar |

    ## Comandos directos de herramienta

    Puedes llamar herramientas directamente desde el chat usando el formato `herramienta: accion, clave=valor`:

    | Comando | Qué hace |
    |---------|----------|
    | `file_manager: read, path=src/main.py` | Leer un archivo |
    | `file_manager: write, path=app.py, content=print('hola')` | Escribir un archivo |
    | `bash_manager: command=pytest tests/` | Ejecutar un comando shell |
    | `git_manager: log, project_root=miproyecto` | Ver historial git |
    | `code_search: pattern=def foo` | Buscar en el código |

    ## Problemas comunes

    ### Errores de "Module not found"

    Ejecuta comandos a través de Poetry: `poetry run python script.py` o `poetry run pytest`.

    ### Conexión a base de datos rechazada

    Verifica que PostgreSQL está corriendo: `pg_isready`. Revisa `DATABASE_URL` en `.env`.

    ### "No API key configured"

    Configura al menos una de: `DEEPSEEK_API_KEY` o `OPENAI_API_KEY` en `.env`. Para modo offline, configura `OFFLINE_MODE=true` y asegúrate de que Ollama está corriendo.

    ### Herramientas no se ejecutan

    Verifica que la herramienta está en la lista `tools.allowed` del workflow. Para comandos directos, la herramienta debe estar registrada globalmente.

    ### Workspace no encontrado

    Los workspaces se crean en el primer uso. Cambia de workspace desde el Dashboard o la configuración. El predeterminado es `main`.
