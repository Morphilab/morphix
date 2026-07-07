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
