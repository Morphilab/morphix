# tests/test_redacting_formatter.py — redacción en logging general
"""RedactingFormatter aplica redact_credentials() a CADA línea del log
general (logs/morphix.log vía basicConfig de run.py), igual que audit.jsonl."""

import logging

from agents.audit import RedactingFormatter

FMT = "%(levelname)s - %(message)s"


def _fmt_record(msg: str, *args: object) -> str:
    formatter = RedactingFormatter(FMT)
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    return formatter.format(record)


def test_raw_token_redacted():
    out = _fmt_record("llamada con token sk-abcdef1234567890abcd fallida")
    assert "sk-abcdef1234567890abcd" not in out
    assert "***" in out
    assert "fallida" in out


def test_key_value_secret_redacted():
    out = _fmt_record("conectando con api_key=supersecreto123 y host ok")
    assert "supersecreto123" not in out
    assert "api_key=***" in out


def test_url_userinfo_redacted():
    out = _fmt_record("postgres://usuario:password123@db:5432/main")
    assert "usuario:password123@" not in out
    assert "://***@" in out


def test_lazy_args_interpolated_then_redacted():
    out = _fmt_record("token recibido: %s", "ghp_abcdefghijklmnopqrstuvwxyz12")
    assert "ghp_" not in out.replace("ghp_", "") or "***" in out
    assert "ghp_abcdefghijklmnopqrstuvwxyz12" not in out


def test_clean_message_untouched():
    line = "INFO esperado - workflow completó 3 subtareas"
    out = _fmt_record(line)
    assert out.endswith(line)


def test_roundtrip_via_real_logger():
    """Comportamiento extremo-a-extremo: handler real con nuestro formatter."""
    import io

    logger = logging.getLogger("test_redact_rt")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(FMT))
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("env var DATABASE_URL=postgresql://u:secreto99@h/db leída")
    out = stream.getvalue()
    assert "secreto99" not in out
    assert "://***@" in out
