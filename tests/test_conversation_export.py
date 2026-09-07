"""Tests de exportación de conversaciones — redacción de secretos."""

import json
from pathlib import Path

import pytest

from desktop.services.conversation_export import export_history_to_file

SECRET = "supersecretvalue99"


def _pdf_text(path: str) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


@pytest.mark.asyncio
async def test_markdown_export_redacts_secrets(tmp_path):
    history = [{"role": "assistant", "content": f"tu api_key = {SECRET}"}]
    out = await export_history_to_file(history, str(tmp_path / "t_redact.md"), "md")
    body = Path(out).read_text(encoding="utf-8")
    assert SECRET not in body
    assert "api_key" in body


@pytest.mark.asyncio
async def test_json_export_redacts_secrets(tmp_path):
    history = [{"role": "user", "content": f"mi token: {SECRET}"}]
    out = await export_history_to_file(history, str(tmp_path / "t_redact.json"), "json")
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert SECRET not in json.dumps(data)


@pytest.mark.asyncio
async def test_html_export_redacts_secrets(tmp_path):
    history = [{"role": "assistant", "content": f"password = {SECRET}"}]
    out = await export_history_to_file(history, str(tmp_path / "t_redact.html"), "html")
    body = Path(out).read_text(encoding="utf-8")
    assert SECRET not in body


@pytest.mark.asyncio
async def test_pdf_export_redacts_secrets(tmp_path):
    pytest.importorskip("reportlab")
    pytest.importorskip("pdfplumber")
    history = [
        {"role": "assistant", "content": f"tu api_key = {SECRET}"},
        {"role": "user", "content": "texto benigno visible"},
    ]
    out = await export_history_to_file(history, str(tmp_path / "t_redact.pdf"), "pdf")
    body = _pdf_text(out)
    assert SECRET not in body
    assert "texto benigno visible" in body


@pytest.mark.asyncio
async def test_raw_token_redacted_in_markdown(tmp_path):
    history = [{"role": "tool", "content": "export KEY=sk-abcdef0123456789abcdef"}]
    out = await export_history_to_file(history, str(tmp_path / "t_token.md"), "md")
    body = Path(out).read_text(encoding="utf-8")
    assert "sk-abcdef0123456789abcdef" not in body


@pytest.mark.asyncio
async def test_sanitize_composes_with_watermark_strip(tmp_path):
    """La redacción no debilita el stripping de watermarks."""
    history = [
        {
            "role": "assistant",
            "content": f"respuesta [ver.baf52ba919] con api_key = {SECRET}",
        },
    ]
    out = await export_history_to_file(history, str(tmp_path / "t_both.md"), "md")
    body = Path(out).read_text(encoding="utf-8")
    assert "ver.baf52ba919" not in body
    assert SECRET not in body


@pytest.mark.asyncio
async def test_html_escapes_content_outside_fences(tmp_path):
    """El contenido fuera de fences va escapado — nada de HTML ejecutable."""
    payload = "<img src=x onerror=alert(1)> <script>alert(2)</script>"
    history = [{"role": "assistant", "content": f"antes {payload} después"}]
    out = await export_history_to_file(history, str(tmp_path / "xss.html"), "html")
    body = Path(out).read_text(encoding="utf-8")
    assert "<img src=x" not in body
    assert "<script>" not in body
    assert "alert(1)" in body  # presente pero INERTe (escapado)


@pytest.mark.asyncio
async def test_html_keeps_pygments_for_fenced_code(tmp_path):
    pytest.importorskip("pygments")
    code = 'print("hola")'
    history = [
        {
            "role": "assistant",
            "content": f"texto seguro\n```python\n{code}\n```\n<script>x</script>",
        }
    ]
    out = await export_history_to_file(history, str(tmp_path / "fence.html"), "html")
    body = Path(out).read_text(encoding="utf-8")
    assert 'class="highlight"' in body or "<pre" in body  # pygments renderiza el fence
    assert "hola" in body
    assert "<script>x</script>" not in body  # lo de fuera sigue escapado
