# tests/test_core_constants.py
"""Los timeouts del sistema deben referenciar core.constants (una sola fuente)."""

import inspect


def test_subtask_timeout_single_source():
    from core.constants import SUBTASK_TIMEOUT_SECONDS
    from orchestration.workflows.development import SUBTASK_TIMEOUT as dev_t
    from orchestration.workflows.orchestrator import SUBTASK_TIMEOUT as orch_t

    assert dev_t is SUBTASK_TIMEOUT_SECONDS
    assert orch_t is SUBTASK_TIMEOUT_SECONDS
    assert SUBTASK_TIMEOUT_SECONDS == 300


def test_tool_call_timeout_single_source():
    import llm.controller
    import tools.wrapper
    from core.constants import TOOL_CALL_TIMEOUT_SECONDS

    wrapper_src = inspect.getsource(tools.wrapper)
    controller_src = inspect.getsource(llm.controller)
    assert "TOOL_CALL_TIMEOUT_SECONDS" in wrapper_src
    assert "TOOL_CALL_TIMEOUT_SECONDS" in controller_src
    assert TOOL_CALL_TIMEOUT_SECONDS == 120


def test_coordinated_and_tdd_use_shared_constant():
    """coordinated.py y tdd.py no deben usar el literal 300 inline."""
    import orchestration.workflows.coordinated as coordinated
    import orchestration.workflows.tdd as tdd

    coordinated_src = inspect.getsource(coordinated.MultiAgentCoordinator)
    tdd_src = inspect.getsource(tdd)
    assert "SUBTASK_TIMEOUT_SECONDS" in coordinated_src
    assert "SUBTASK_TIMEOUT_SECONDS" in tdd_src
