# tests/test_vision_analyze.py — tool vision_analyze (rol 'vision')
"""Tool vision_analyze: describe/OCR/chart de imágenes vía rol 'vision'.
Resultado SIEMPRE texto; detección por magic bytes; containment anti
path-traversal patrón pdf_reader; cap 32 MiB."""

import base64

import pytest

from tools.specs import TOOL_DEFINITIONS, tool_matches_allowlist
from tools.vision_analyze import _detect_format, _load_image, vision_analyze


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """Redirige el directorio del proyecto a tmp."""
    import core.path_resolver as pr

    base = tmp_path / "proj"
    base.mkdir()
    monkeypatch.setattr(
        pr.paths,
        "code_projects_dir",
        lambda ws, root=None: base,
    )
    return base


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
JPEG = b"\xff\xd8\xff" + b"e0" + b"y" * 20


# ── _detect_format ────────────────────────────────────────────────────────


def test_detect_format_by_magic_bytes():
    assert _detect_format(PNG) == "image/png"
    assert _detect_format(JPEG) == "image/jpeg"
    assert _detect_format(b"GIF87a" + b"z" * 10) == "image/gif"
    assert _detect_format(b"GIF89a" + b"z" * 10) == "image/gif"
    assert _detect_format(b"RIFF\xf0\xb0\xba\xd1WEBPVP8 ") == "image/webp"


def test_detect_format_rejects_non_image():
    # Extensión mintida: contenido texto con nombre .png debe ser None.
    assert _detect_format(b"<html>no soy imagen</html>") is None
    assert _detect_format(b"") is None


# ── _load_image ───────────────────────────────────────────────────────────


def test_load_image_ok_returns_b64_and_mime(proj):
    f = proj / "foto.png"
    f.write_bytes(PNG)
    b64, mime = _load_image(str(f), proj)
    assert mime == "image/png"
    assert base64.b64decode(b64) == PNG


def test_load_image_rejects_path_traversal(proj):
    outside = proj.parent / "secreto.png"
    outside.write_bytes(PNG)
    out = _load_image(str(outside), proj)
    assert isinstance(out, str)
    assert "fuera" in out.lower() or "denegado" in out.lower()


def test_load_image_rejects_missing_file(proj):
    out = _load_image(str(proj / "noexiste.png"), proj)
    assert isinstance(out, str) and "no encontrado" in out.lower()


def test_load_image_rejects_oversize(proj, monkeypatch):
    f = proj / "grande.png"
    f.write_bytes(PNG)
    import tools.vision_analyze as va

    monkeypatch.setattr(va, "MAX_IMAGE_BYTES", 4)
    out = _load_image(str(f), proj)
    assert (
        isinstance(out, str)
        and "32" not in out
        and ("demasiad" in out.lower() or "tamañ" in out.lower())
    )


def test_load_image_rejects_non_image_content(proj):
    f = proj / "falso.png"
    f.write_bytes(b"texto plano")
    out = _load_image(str(f), proj)
    assert isinstance(out, str) and "imagen" in out.lower()


# ── vision_analyze (mock de models.call) ──────────────────────────────────


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


@pytest.mark.asyncio
async def test_describe_mode_sends_blocks_and_returns_text(proj, monkeypatch):
    f = proj / "diag.png"
    f.write_bytes(PNG)

    captured = {}

    async def fake_call(messages, role="default", **kw):
        captured["messages"] = messages
        captured["role"] = role
        return _Resp("un diagrama de flujo")

    import llm.controller as ctrl

    monkeypatch.setattr(ctrl.models, "call", fake_call)

    out = await vision_analyze(path=str(f), project_root="")
    assert out == "un diagrama de flujo"
    assert captured["role"] == "vision"
    msg = captured["messages"][0]
    assert msg["role"] == "user"
    blocks = msg["content"]
    assert isinstance(blocks, list)
    types = [b["type"] for b in blocks]
    assert "text" in types and "image_url" in types
    img_block = next(b for b in blocks if b["type"] == "image_url")
    url = img_block["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_ocr_and_chart_modes_use_own_prompts(proj, monkeypatch):
    f = proj / "scan.png"
    f.write_bytes(JPEG)
    seen = []

    async def fake_call(messages, role="default", **kw):
        text_block = messages[0]["content"][0]
        seen.append(text_block["text"])
        return _Resp("ok")

    import llm.controller as ctrl

    monkeypatch.setattr(ctrl.models, "call", fake_call)

    await vision_analyze(path=str(f), mode="ocr", project_root="")
    await vision_analyze(path=str(f), mode="chart", project_root="")
    await vision_analyze(path=str(f), mode="describe", project_root="")
    assert len(set(seen)) == 3, "cada modo debe tener prompt propio"


@pytest.mark.asyncio
async def test_prompt_override_wins(proj, monkeypatch):
    f = proj / "x.png"
    f.write_bytes(PNG)

    async def fake_call(messages, role="default", **kw):
        return _Resp(messages[0]["content"][0]["text"])

    import llm.controller as ctrl

    monkeypatch.setattr(ctrl.models, "call", fake_call)
    out = await vision_analyze(path=str(f), mode="ocr", prompt="cuenta los gatos", project_root="")
    assert out == "cuenta los gatos"


@pytest.mark.asyncio
async def test_invalid_mode_actionable_error(proj):
    f = proj / "x.png"
    f.write_bytes(PNG)
    out = await vision_analyze(path=str(f), mode="traducir", project_root="")
    assert "❌" in out and "describe" in out and "ocr" in out and "chart" in out


@pytest.mark.asyncio
async def test_llm_error_surfaces_as_string(proj, monkeypatch):
    f = proj / "x.png"
    f.write_bytes(PNG)

    async def boom(*a, **k):
        raise RuntimeError("proveedor caído")

    import llm.controller as ctrl

    monkeypatch.setattr(ctrl.models, "call", boom)
    out = await vision_analyze(path=str(f), project_root="")
    assert "❌" in out and "proveedor caído" in out


# ── Registro y spec (#14 → exposición MCP automática) ─────────────────────


def test_spec_14_registered_and_allowlist_matchable():
    spec = TOOL_DEFINITIONS.get("vision_analyze")
    assert spec, "vision_analyze ausente de TOOL_DEFINITIONS"
    assert spec.required == ["path"]
    assert set(spec.parameters) >= {"path", "mode", "prompt"}
    mode_param = spec.parameters["mode"]
    assert mode_param.get("enum") == ["describe", "ocr", "chart"]
    assert tool_matches_allowlist("vision_analyze", ["vision_analyze"])
