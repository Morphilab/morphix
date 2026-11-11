# tests/test_core_constants.py
"""Los timeouts del sistema deben referenciar core.constants (una sola fuente)."""

import inspect


def test_tool_call_timeout_single_source():
    import llm.controller
    import tools.wrapper
    from core.constants import TOOL_CALL_TIMEOUT_SECONDS

    wrapper_src = inspect.getsource(tools.wrapper)
    controller_src = inspect.getsource(llm.controller)
    assert "TOOL_CALL_TIMEOUT_SECONDS" in wrapper_src
    assert "TOOL_CALL_TIMEOUT_SECONDS" in controller_src
    assert TOOL_CALL_TIMEOUT_SECONDS == 120
