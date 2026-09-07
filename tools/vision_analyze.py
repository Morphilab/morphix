"""Vision analyze — describe/OCR/gráficos vía rol 'vision'.

Resultado SIEMPRE texto hacia dentro (cero cambios de esquema DB/workflows/
GUI). Detección de formato por magic bytes (nunca extensión); containment
anti path-traversal patrón pdf_reader; cap 32 MiB inline de DeepSeek.
"""

import asyncio
import base64
import logging

from agents.audit import log_operation
from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 32 * 1024 * 1024

_MODES = {
    "describe": "Describe esta imagen de forma detallada y precisa: objetos, texto visible, composición y cualquier dato relevante para un agente de software.",
    "ocr": "Extrae TODO el texto visible en esta imagen, preservando la estructura (líneas, tablas, jerarquías). No añadas comentarios ni resúmenes.",
    "chart": "Analiza este gráfico: tipo de gráfico, ejes y unidades, series de datos con valores aproximados legibles, y las 2-3 conclusiones más importantes.",
}

_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _detect_format(data: bytes) -> str | None:
    """MIME por magic bytes; None si no es imagen reconocible."""
    for sig, mime in _MAGIC_SIGNATURES:
        if data.startswith(sig):
            return mime
    # WebP: RIFF....WEBP (offset 8)
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _load_image(path: str, base) -> "tuple[str, str] | str":
    """(b64, mime) del archivo validado; string ❌ accionable si falla."""
    from pathlib import Path

    resolved = (Path(base) / path).resolve()
    try:
        resolved.relative_to(Path(base).resolve())
    except ValueError:
        return f"❌ Acceso denegado: {path} está fuera del workspace."

    if not resolved.exists():
        return f"❌ Archivo no encontrado: {resolved}"

    data = resolved.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        return f"❌ Imagen demasiado grande ({len(data)} bytes; máximo {MAX_IMAGE_BYTES})."

    mime = _detect_format(data)
    if mime is None:
        return (
            "❌ El archivo no parece una imagen soportada (JPEG/PNG/GIF/WebP) según su contenido."
        )

    return base64.b64encode(data).decode("ascii"), mime


def tools_registry_dec(name: str):
    """Registro diferido del tool (evita import circular top-level)."""
    from tools.registry import tools_registry

    def deco(fn):
        return tools_registry.register(name)(fn)

    return deco


@tools_registry_dec("vision_analyze")
async def vision_analyze(
    path: str,
    mode: str = "describe",
    prompt: str | None = None,
    workspace: str | None = None,
    project_root: str | None = None,
) -> str:
    """Analiza una imagen con el rol 'vision' y retorna SIEMPRE texto."""
    if mode not in _MODES:
        return (
            f"❌ mode '{mode}' no soportado. Usa: describe, ocr, chart "
            "(o prompt libre para override)."
        )

    if workspace is None:
        workspace = settings.active_workspace
    base = paths.code_projects_dir(workspace, project_root)

    loaded = await asyncio.to_thread(_load_image, path, base)
    if isinstance(loaded, str):
        return loaded
    b64, mime = loaded

    effective_prompt = prompt or _MODES[mode]
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": effective_prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ],
    }

    from llm.controller import models

    try:
        response = await models.call(messages=[message], role="vision", stream=False)
        content = response.choices[0].message.content
    except Exception as e:  # noqa: BLE001 — error accionable al LLM llamador
        logger.warning("vision_analyze falló: %s", e)
        log_operation("vision_analyze", str(path)[:200], success=False)
        return f"❌ El análisis de visión falló: {e}"

    log_operation("vision_analyze", str(path)[:200], success=True)
    return str(content)
