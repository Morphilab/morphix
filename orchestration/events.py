"""Capa de eventos — re-exporta desde orchestration/context.py.

Mantenido por backward compat. El código nuevo debe importar desde orchestration.context.
"""

from orchestration.context import (  # noqa: F401
    Session,
    WorkflowContext,
    WorkflowEvents,
    _emit,
    emit_agent,
    emit_assistant,
    emit_diagram,
    emit_refresh,
    emit_stats,
    emit_stream_chunk,
    emit_system,
    emit_user,
)
