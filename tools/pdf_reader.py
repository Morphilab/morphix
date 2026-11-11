# tools/pdf_reader.py

import asyncio
import logging

import pdfplumber

from agents.audit import log_operation
from core.config import settings
from core.path_resolver import paths
from tools.registry import tools_registry

logger = logging.getLogger(__name__)


class PDFReader:
    @staticmethod
    def read_pdf(file_path: str) -> str:
        try:
            text = ""
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return (
                text.strip()
                if text.strip()
                else "No se pudo extraer texto del PDF (puede ser imagen)."
            )
        except Exception as e:
            return f"Error leyendo PDF: {e!s}"


@tools_registry.register("pdf_read")
async def pdf_read_tool(
    path: str,
    workspace: str | None = None,
    project_root: str | None = None,
    **kwargs,
) -> str:
    """Extrae texto de archivos PDF con validación de path traversal."""
    if workspace is None:
        workspace = settings.active_workspace
    base = paths.code_projects_dir(workspace, project_root).resolve()

    resolved = (base / path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return f"❌ Acceso denegado: {path} está fuera del workspace."

    if not resolved.exists():
        return f"❌ Archivo no encontrado: {resolved}"

    result = await asyncio.to_thread(PDFReader.read_pdf, str(resolved))
    log_operation("pdf_read", str(resolved)[:200], success=not result.startswith("Error"))
    return result
