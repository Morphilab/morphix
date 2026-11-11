# tests/test_pdf_reader.py

import pytest


class TestPdfReaderTool:
    async def test_read_pdf_path_traversal_blocked(self, tmp_path):
        """Verifica que un path fuera del workspace sea bloqueado."""
        from tools.pdf_reader import pdf_read_tool

        result = await pdf_read_tool(
            path="../../../etc/passwd",
            workspace="main",
            project_root="code_projects/miapp",
        )
        assert "Acceso denegado" in result or "fuera" in result.lower()

    async def test_read_pdf_file_not_found(self, tmp_path):
        """Archivo inexistente dentro del workspace."""
        from tools.pdf_reader import pdf_read_tool

        result = await pdf_read_tool(
            path="no_existe.pdf",
            workspace="main",
            project_root="code_projects/miapp",
        )
        assert "no encontrado" in result.lower() or "Acceso denegado" in result

    def test_pdf_reader_class_extracts_text(self, tmp_path):
        """Prueba la clase PDFReader con un PDF temporal."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
        except ImportError:
            pytest.skip("reportlab no disponible")

        pdf_path = tmp_path / "test.pdf"
        c = canvas.Canvas(str(pdf_path), pagesize=letter)
        c.drawString(100, 750, "Hola mundo desde PDF")
        c.save()

        from tools.pdf_reader import PDFReader

        result = PDFReader.read_pdf(str(pdf_path))
        assert "Hola mundo" in result


class TestPdfReaderToolIntegration:
    async def test_read_pdf_with_valid_path(self, tmp_path):
        """Lee un PDF dentro del workspace usando la tool completa."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
        except ImportError:
            pytest.skip("reportlab no disponible")

        # Simular estructura de directorios
        ws_dir = tmp_path / "memory" / "main" / "code_projects" / "miapp"
        ws_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = ws_dir / "doc.pdf"
        c = canvas.Canvas(str(pdf_path), pagesize=letter)
        c.drawString(100, 750, "Contenido del PDF de prueba")
        c.save()

        # Parchar paths para usar tmp_path
        from unittest.mock import patch

        from core.path_resolver import paths

        with patch.object(paths, "code_projects_dir", return_value=ws_dir):
            from tools.pdf_reader import pdf_read_tool

            result = await pdf_read_tool(
                path="doc.pdf",
                workspace="main",
                project_root="code_projects/miapp",
            )
            assert "Contenido del PDF" in result
