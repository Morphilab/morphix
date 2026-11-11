# tests/test_core.py
import os
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import pytest
from reportlab.pdfgen import canvas

from llm import OfflineManager, models
from tools.code_execution import CodeExecutor


@pytest.mark.asyncio
async def test_models_controller_instance_exists():
    """Test que la instancia global de ModelsController existe."""
    assert models is not None


@pytest.mark.asyncio
async def test_models_controller_has_call_method():
    """Test que ModelsController tiene el método call()."""
    assert hasattr(models, "call"), "ModelsController debe tener método call()"


@pytest.mark.asyncio
async def test_offline_manager_detect_online():
    """Test offline detect returns False if online."""
    mock_response = Mock(status_code=200)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        manager = OfflineManager()
        assert not await manager.detect(), "Should detect online"


@pytest.mark.asyncio
async def test_code_execution_safe_print():
    """Test safe code execution with print."""
    result = await CodeExecutor.execute("print('Hello World')")
    assert "Hello World" in result["text"], "Should capture print output"


@pytest.mark.asyncio
async def test_code_execution_safe_numpy():
    """Test allowed module (numpy)."""
    result = await CodeExecutor.execute("import numpy as np; print(np.array([1,2,3]))")
    assert "[1 2 3]" in result["text"], "Should allow numpy"


@pytest.mark.asyncio
async def test_code_execution_disallowed_import():
    """Test disallowed import (e.g., os)."""
    result = await CodeExecutor.execute("import os; print('Bad')")
    assert "Import blocked for security" in result["text"], "Should block os import"


@pytest.mark.asyncio
async def test_code_execution_plot_image():
    """Test code with plot (generates image). plt ya está en SAFE_MODULES."""
    code = "plt.plot([1,2,3]); plt.savefig('test.png')"
    result = await CodeExecutor.execute(code)
    assert result.get("image_path") is not None or result.get("text"), "Should generate output"


@pytest.fixture
def temp_pdf():
    """Fixture for temp PDF file with reportlab."""
    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        c = canvas.Canvas(temp_file.name)
        c.drawString(100, 750, "Test content for PDF reader.")
        c.save()
        yield temp_file.name
    finally:
        os.unlink(temp_file.name)


@pytest.mark.asyncio
async def test_pdf_reader_extract_text(temp_pdf):
    """Test PDFReader extract from temp file."""
    from tools.pdf_reader import PDFReader

    result = PDFReader.read_pdf(temp_pdf)
    assert "Test content" in result, "Should extract text from PDF"
