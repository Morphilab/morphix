# tests/test_result_aggregator.py
from unittest.mock import AsyncMock, MagicMock, patch

import networkx as nx
import pytest


def _make_graph(tasks: list[str]) -> nx.DiGraph:
    G = nx.DiGraph()
    for i, t in enumerate(tasks):
        G.add_node(i, task=t)
    return G


@pytest.fixture
def mock_models_call():
    with patch("orchestration.aggregator.models.call", new_callable=AsyncMock) as mock_call:
        yield mock_call


@pytest.mark.asyncio
async def test_aggregate_empty_results():
    from orchestration.aggregator import ResultAggregator

    G = _make_graph(["t1"])
    result = await ResultAggregator.aggregate_results("query", {}, G, {})
    assert "⚠️" in result


@pytest.mark.asyncio
async def test_aggregate_single_result_with_synthesis(mock_models_call):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Síntesis final del proyecto Flask."
    mock_models_call.return_value = response

    from orchestration.aggregator import ResultAggregator

    G = _make_graph(["Crear proyecto Flask"])
    results = {0: {"result": "Proyecto creado correctamente.", "status": "completed"}}
    result = await ResultAggregator.aggregate_results("Crea un proyecto Flask", results, G, {})
    assert "Síntesis final" in result


@pytest.mark.asyncio
async def test_aggregate_multiple_results_with_synthesis(mock_models_call):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Resumen combinado de 3 subtareas."
    mock_models_call.return_value = response

    from orchestration.aggregator import ResultAggregator

    G = _make_graph(["app.py", "test_app.py", "Git init"])
    results = {
        0: {"result": "app.py creado.", "status": "completed"},
        1: {"result": "test_app.py creado.", "status": "completed"},
        2: {"result": "Git inicializado.", "status": "completed"},
    }
    result = await ResultAggregator.aggregate_results("Crea proyecto Flask", results, G, {})
    assert "Resumen combinado" in result


@pytest.mark.asyncio
async def test_aggregate_fallback_on_empty_llm_response(mock_models_call):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = ""
    mock_models_call.return_value = response

    from orchestration.aggregator import ResultAggregator

    G = _make_graph(["Crear app.py"])
    results = {0: {"result": "Archivo creado.", "status": "completed"}}
    result = await ResultAggregator.aggregate_results("Crea un archivo", results, G, {})
    assert "**Consulta:**" in result


@pytest.mark.asyncio
async def test_aggregate_fallback_on_useless_llm_response(mock_models_call):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "No se incluye información relevante."
    mock_models_call.return_value = response

    from orchestration.aggregator import ResultAggregator

    G = _make_graph(["Crear app.py"])
    results = {0: {"result": "Archivo creado.", "status": "completed"}}
    result = await ResultAggregator.aggregate_results("Crea un archivo", results, G, {})
    assert "**Consulta:**" in result


@pytest.mark.asyncio
async def test_aggregate_fallback_on_exception(mock_models_call):
    mock_models_call.side_effect = RuntimeError("API error")

    from orchestration.aggregator import ResultAggregator

    G = _make_graph(["Crear app.py"])
    results = {0: {"result": "Archivo creado.", "status": "completed"}}
    result = await ResultAggregator.aggregate_results("Crea un archivo", results, G, {})
    assert "**Consulta:**" in result


class TestConformance:
    @pytest.mark.asyncio
    async def test_detects_missing_loop(self, tmp_path, monkeypatch):
        """Tarea con 'bucle' pero archivo sin for/while → violación."""
        from orchestration import aggregator

        (tmp_path / "code_projects" / "prueba6").mkdir(parents=True)
        (tmp_path / "code_projects/prueba6" / "numeros.py").write_text(
            "print(1)\nprint(2)\nprint(3)\n"
        )
        monkeypatch.setattr(aggregator._paths, "memory_dir", lambda ws: tmp_path)

        violations = await aggregator.ResultAggregator._conformance_violations(
            "Imprime los números del 1 al 5 usando un bucle",
            ["numeros.py"],
            "code_projects/prueba6",
            "main",
        )
        assert any("bucle" in v for v in violations)

    @pytest.mark.asyncio
    async def test_passes_when_loop_present(self, tmp_path, monkeypatch):
        """Tarea con 'bucle' y archivo con for → sin violaciones."""
        from orchestration import aggregator

        (tmp_path / "code_projects" / "prueba6").mkdir(parents=True)
        (tmp_path / "code_projects/prueba6" / "numeros.py").write_text(
            "for numero in range(1, 6):\n    print(numero)\n"
        )
        monkeypatch.setattr(aggregator._paths, "memory_dir", lambda ws: tmp_path)

        violations = await aggregator.ResultAggregator._conformance_violations(
            "Imprime los números del 1 al 5 usando un bucle",
            ["numeros.py"],
            "code_projects/prueba6",
            "main",
        )
        assert violations == []

    @pytest.mark.asyncio
    async def test_no_keywords_no_violations(self, tmp_path, monkeypatch):
        """Tarea sin keywords de requisito → sin violaciones."""
        from orchestration import aggregator

        (tmp_path / "code_projects" / "app").mkdir(parents=True)
        (tmp_path / "code_projects/app" / "app.py").write_text("print('hola')\n")
        monkeypatch.setattr(aggregator._paths, "memory_dir", lambda ws: tmp_path)

        violations = await aggregator.ResultAggregator._conformance_violations(
            "Crea un script que salude",
            ["app.py"],
            "code_projects/app",
            "main",
        )
        assert violations == []

    @pytest.mark.asyncio
    async def test_success_downgraded_to_partial_on_violation(
        self, tmp_path, monkeypatch, mock_models_call
    ):
        """El veredicto 'success' baja a 'partial' si el archivo viola un
        requisito explícito (evidencia 2026-08-14: numeros.py sin bucle
        reportado como 'completo y funcional')."""
        from unittest.mock import AsyncMock, patch

        from orchestration import aggregator

        (tmp_path / "code_projects" / "prueba6").mkdir(parents=True)
        (tmp_path / "code_projects/prueba6" / "numeros.py").write_text(
            "print(1)\nprint(2)\nprint(3)\n"
        )
        monkeypatch.setattr(aggregator._paths, "memory_dir", lambda ws: tmp_path)

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Síntesis con advertencia de conformidad."
        mock_models_call.return_value = response

        with patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            mock_loop.return_value = {"result": "Síntesis con advertencia de conformidad."}

            G = _make_graph(["Crear numeros.py"])
            results = {0: {"result": "numeros.py creado.", "status": "completed"}}
            result = await aggregator.ResultAggregator.aggregate_results(
                "Imprime los números del 1 al 5 usando un bucle",
                results,
                G,
                {},
                files_written=["numeros.py"],
                project_root="code_projects/prueba6",
                workspace="main",
            )
        assert "completo y funcional" not in result
        mock_loop.assert_awaited_once()
