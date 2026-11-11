# orchestration/dsl/run_journal.py
"""Journal durable de un run DSL (best-effort, sin secretos ni tails crudos).

El snapshot de pausa solo existe si el run pausa; el journal deja evidencia de
TODOS los runs en ``memory/<ws>/runs/<run_id>.json`` para el post-mortem y la
próxima sesión de análisis. Un fallo de disco jamás tumba el workflow.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.path_resolver import paths
from orchestration.dsl.run_state import RunState

logger = logging.getLogger(__name__)

_MAX_BYTES = 100_000


class RunJournal:
    def __init__(self, workspace: str):
        self.workspace = workspace

    def write(
        self,
        run_state: RunState,
        scorecard: dict | None,
        *,
        partial: bool = False,
        step: int = 0,
    ) -> Path | None:
        """Escribe el journal; `partial` = snapshot de progreso (crash-safe).

        El cierre lo reescribe con el scorecard final; un parcial previo no
        bloquea el run ni se confunde con el cierre (`partial: true`).
        """
        if not run_state.run_id:
            return None
        try:
            base = paths.memory_dir(self.workspace) / "runs"
            base.mkdir(parents=True, exist_ok=True)
            payload = {**run_state.public(), "scorecard": scorecard}
            if partial:
                payload["partial"] = True
                payload["step"] = step
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            if len(text.encode("utf-8")) > _MAX_BYTES:
                logger.warning(
                    "journal del run %s excede %d bytes; se omite",
                    run_state.run_id,
                    _MAX_BYTES,
                )
                return None
            target = base / f"{run_state.run_id}.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
            return target
        except Exception:
            logger.warning(
                "journal del run %s no escrito (best-effort)",
                run_state.run_id,
                exc_info=True,
            )
            return None
