# tests/test_git_manager.py
import pytest

from tools.git_manager import GitManager


@pytest.fixture
def temp_memory(tmp_path, monkeypatch):
    """Redirige Path('memory') a un directorio temporal vía path_resolver."""
    fake_memory = tmp_path / "memory"
    fake_memory.mkdir()

    import core.path_resolver as pr

    monkeypatch.setattr(pr, "MEMORY_BASE", tmp_path / "memory")
    monkeypatch.setattr(pr.paths, "memory_dir", lambda ws: tmp_path / "memory" / ws)
    monkeypatch.setattr(pr.paths, "memory_base", lambda: tmp_path / "memory")
    yield tmp_path / "memory"


@pytest.mark.asyncio
async def test_init_creates_repo(temp_memory):
    """Inicializa un repositorio Git en el directorio del proyecto."""
    await GitManager.execute(
        "init",
        workspace="main",
        project_root="code_projects/miapp",
    )
    repo_path = temp_memory / "main" / "code_projects" / "miapp"
    assert (repo_path / ".git").is_dir()


@pytest.mark.asyncio
async def test_add_and_commit_after_init(temp_memory):
    """Añade y commitea un archivo en un repo ya inicializado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    # Crear un archivo en el directorio del proyecto
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")
    # Add y commit
    add_result = await GitManager.execute(
        "add", workspace="main", project_root="code_projects/miapp"
    )
    assert "Archivos añadidos" in add_result
    commit_result = await GitManager.execute(
        "commit",
        message="commit inicial",
        workspace="main",
        project_root="code_projects/miapp",
    )
    # '': retorno estructurado
    assert isinstance(commit_result, dict) and commit_result["success"] is True
    assert "Commit realizado" in commit_result["output"]


@pytest.mark.asyncio
async def test_commit_without_init_fails(temp_memory):
    """Commit sin inicializar repo devuelve error."""
    result = await GitManager.execute(
        "commit",
        message="fallará",
        workspace="main",
        project_root="code_projects/miapp",
    )
    assert "No hay un repositorio Git inicializado" in result


@pytest.mark.asyncio
async def test_missing_project_root_error(temp_memory):
    """Si no se especifica project_root, devuelve error."""
    result = await GitManager.execute("init", workspace="main")
    assert "git_manager necesita 'project_root'" in result


@pytest.mark.asyncio
async def test_commit_rejects_error_message(temp_memory):
    """Commit con mensaje de error del sistema debe ser rechazado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")
    await GitManager.execute("add", workspace="main", project_root="code_projects/miapp")

    result = await GitManager.execute(
        "commit",
        message="❌ Rate limit excedido. Intenta de nuevo en unos segundos.",
        workspace="main",
        project_root="code_projects/miapp",
    )
    assert "no válido" in result


@pytest.mark.asyncio
async def test_commit_accepts_valid_message(temp_memory):
    """Commit con mensaje válido debe ser aceptado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")
    await GitManager.execute("add", workspace="main", project_root="code_projects/miapp")

    result = await GitManager.execute(
        "commit",
        message="feat: agregar endpoint JWT",
        workspace="main",
        project_root="code_projects/miapp",
    )
    # '': dict estructurado; texto conservado en 'output'
    assert result["success"] is True
    assert "Commit realizado" in result["output"]


@pytest.mark.asyncio
async def test_show_returns_file_content(temp_memory):
    """Task 2.1 doc 4: acción show read-only devuelve contenido de git show."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido-marcador")
    await GitManager.execute("add", workspace="main", project_root="code_projects/miapp")
    await GitManager.execute(
        "commit", message="feat: inicial", workspace="main", project_root="code_projects/miapp"
    )

    result = await GitManager.execute(
        "show",
        workspace="main",
        project_root="code_projects/miapp",
        ref_path="HEAD:test.txt",
    )
    assert "contenido-marcador" in str(result)


@pytest.mark.asyncio
async def test_show_requires_ref_path(temp_memory):
    """show sin ref_path → error claro (no crashea)."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    result = await GitManager.execute("show", workspace="main", project_root="code_projects/miapp")
    assert "ref_path" in str(result)


def test_show_not_in_dangerous_actions():
    """show es read-only: NO exige diálogo de aprobación."""
    from tools.orchestrator import ToolOrchestrator

    assert "git_manager.show" not in ToolOrchestrator.DANGEROUS_ACTIONS


def test_phantom_push_removed_from_dangerous_actions():
    """git_manager.push no existe como implementación — fuera del set."""
    from tools.orchestrator import ToolOrchestrator

    assert "git_manager.push" not in ToolOrchestrator.DANGEROUS_ACTIONS
    # y la acción push realmente no está implementada:
    from tools.git_manager import GitManager

    src = open(GitManager.execute.__code__.co_filename, encoding="utf-8").read()
    assert 'action == "push"' not in src, "push implementado → re-añadir a DANGEROUS_ACTIONS"


def test_spec_has_no_phantom_status_and_documents_show():
    """Enum sin status fantasma; show documentado con ref_path."""
    from tools.specs import TOOL_DEFINITIONS

    params = TOOL_DEFINITIONS["git_manager"].parameters
    actions = params["action"]["enum"]
    assert "status" not in actions
    assert "show" in actions
    assert "ref_path" in params


@pytest.mark.asyncio
async def test_git_manager_without_action_fails_clearly(temp_memory):
    """Sin default fantasma — action vacío da error explícito."""
    result = await GitManager.execute("", workspace="main", project_root="code_projects/miapp")
    assert "action" in str(result).lower()
