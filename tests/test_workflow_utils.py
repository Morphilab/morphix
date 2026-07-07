# tests/test_workflow_utils.py
import networkx as nx


def _make_graph(tasks: list[str]) -> nx.DiGraph:
    G = nx.DiGraph()
    for i, t in enumerate(tasks):
        G.add_node(i, task=t)
    return G


def test_clean_generated_code_removes_code_fences():
    from orchestration.utils import clean_generated_code

    raw = "```python\nfrom flask import Flask\napp = Flask(__name__)\n```"
    result = clean_generated_code(raw)
    assert "```" not in result
    assert "from flask import Flask" in result


def test_clean_generated_code_removes_intro_phrases():
    from orchestration.utils import clean_generated_code

    raw = "Aquí tienes el código:\nfrom flask import Flask"
    result = clean_generated_code(raw)
    assert "Aquí tienes" not in result


def test_clean_generated_code_empty_string():
    from orchestration.utils import clean_generated_code

    result = clean_generated_code("")
    assert result == ""


def test_generate_scorecard_basic():
    from orchestration.utils import generate_scorecard

    G = _make_graph(["t1", "t2"])
    results = {
        0: {"result": "Archivo creado.", "status": "completed"},
        1: {"result": "Error en test.", "status": "failed"},
    }
    score = generate_scorecard(results, G, "final", "query", {}, 0.0)
    assert score["subtasks"] == 2
    assert score["completadas"] == 1
    assert score["fallidas"] == 1


def test_generate_scorecard_empty_results():
    from orchestration.utils import generate_scorecard

    G = _make_graph([])
    score = generate_scorecard({}, G, "", "", {}, 0.0)
    assert score["subtasks"] == 0
    assert score["completadas"] == 0
