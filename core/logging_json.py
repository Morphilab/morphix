# core/logging_json.py — logs estructurados (JSON lines) opt-in.
"""Formatter JSON para el root logger (LOG_JSON=true).

Una línea por evento: telemetría y greps estructurados sin depender del
formato humano. Los consumidores existentes de texto no cambian — el opt-in
reemplaza SOLO el formatter de los handlers del root.
"""

import datetime
import json
import logging

from agents.audit import redact_credentials


class JsonFormatter(logging.Formatter):
    """Un registro → una línea JSON: ts, level, logger, message (+exc_info).

    La redacción de credenciales es parte del contrato (logs/morphix.log
    redacta SIEMPRE — el modo JSON no puede ser la excepción): message y
    exc_info pasan por redact_credentials antes de serializarse.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.datetime.now(datetime.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_credentials(record.getMessage()),
        }
        if record.exc_info:
            payload["exc_info"] = redact_credentials(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


def apply_json_logging(root: logging.Logger | None = None) -> None:
    """Sustituye el formatter de TODO handler del root logger por JSON."""
    root = root or logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())
