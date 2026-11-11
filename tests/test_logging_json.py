# tests/test_logging_json.py — el modo LOG_JSON produce líneas JSON
# REDACTADAS (la redacción de credenciales es parte del contrato del log).
import json
import logging

from core.logging_json import JsonFormatter, apply_json_logging


def _record(msg: str, **kw) -> logging.LogRecord:
    return logging.LogRecord(
        name="morphix.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=kw.get("exc_info"),
        func=None,
        sinfo=None,
    )


def test_linea_json_parseable_con_claves():
    out = JsonFormatter().format(_record("hola mundo"))
    payload = json.loads(out)
    assert payload["message"] == "hola mundo"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "morphix.test"
    assert "ts" in payload


def test_linea_json_redacta_credenciales():
    record = _record("login con api_key=sk-1234567890abcdef y token ghp_aabbccddeeff0011")
    out = JsonFormatter().format(record)
    assert "sk-1234567890abcdef" not in out, "el JSON sin redactor es un agujero de seguridad"
    assert "ghp_aabbccddeeff0011" not in out
    assert "REDACT" in out or "***" in out


def test_exc_info_serializado_y_redactado():
    try:
        raise ValueError("boom con clave sk-999888777666555")
    except ValueError:
        import sys

        record = _record("falló", exc_info=sys.exc_info())
    out = JsonFormatter().format(record)
    payload = json.loads(out)
    assert "ValueError" in payload["exc_info"]
    assert "sk-999888777666555" not in out


def test_apply_json_logging_reemplaza_handlers_del_root():
    import io

    root = logging.getLogger("json_test_root")
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    apply_json_logging(root)
    assert isinstance(handler.formatter, JsonFormatter)
    root.removeHandler(handler)
