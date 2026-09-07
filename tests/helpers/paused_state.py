# tests/helpers/paused_state.py
"""Helper compartido para construir paused_state realista en tests de resume.

Replica la parte del schema de ``paused_state`` que los tests ejercitan
(la produccion escribe mas campos — p.ej. ``G_nodes``, ``blackboard_snapshot`` —
pero el resume los lee con ``.get()``). Si el schema cambia, actualizar SOLO aqui:
- origin="development" → estado del workflow development (subtask_index + loop state).
- origin="coordinated" → estado del workflow coordinated (DAG con phases + resultado
  pausado).

El import ``from helpers.paused_state import ...`` funciona porque pytest inyecta
``tests/`` en sys.path (no es paquete de proyecto) — no "arreglarlo".
"""

import json


def make_paused_state(
    origin: str, *, subtasks: list[str], subtask_index: int = 0, **overrides
) -> str:
    # **overrides reemplazan claves top-level del estado tras la construccion.
    if origin == "development":
        state = {
            "subtask_index": subtask_index,
            "subtasks": subtasks,
            "results": {},
            "corrected_agents": ["developer"],
            "conversation_history": [],
            "task_analysis": {"primary_type": "development"},
            "paused_loop_state": {
                "messages": [],
                "task": subtasks[subtask_index] if subtasks else "",
                "agent_type": "developer",
                "allowed_tools": ["file_manager"],
            },
        }
    elif origin == "coordinated":
        subtask_id = f"default_{subtask_index}"
        state = {
            "origin": "coordinated",
            "query": "task",
            "phases": [{"phase": "default", "order": 1, "subtasks": subtasks}],
            "phase_index": 0,
            "clarification_subtask_id": subtask_id,
            "results": {
                subtask_id: {
                    "status": "clarification_needed",
                    "clarification_question": "¿Qué framework?",
                    "clarification_options": [],
                    "paused_loop_state": {
                        "messages": [],
                        "task": subtasks[subtask_index] if subtasks else "",
                        "agent_type": "developer",
                        "allowed_tools": ["file_manager"],
                    },
                    "agent": "developer",
                    "files_written": [],
                }
            },
            "blackboard_snapshot": {},
            "conversation_history": [],
        }
    else:
        raise ValueError(f"origin desconocido: {origin}")
    state.update(overrides)
    return json.dumps(state)
