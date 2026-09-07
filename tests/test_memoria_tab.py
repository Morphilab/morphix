"""MemoriaService + MemoriaTab — tab GUI para memory_inspector.

El servicio envuelve los handlers de tools/memory_inspector.py; los tests
siembran `memory.documents` directamente (singleton in-memory).
"""

from typing import cast
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6.QtWidgets")

from core.memory.manager import memory as _mem  # noqa: E402
from desktop.services.memoria_service import (  # noqa: E402
    delete_key,
    list_keys,
    read_key,
)


@pytest.fixture()
def seeded():
    _mem.documents.clear()
    _mem.documents.append(("perfil_usuario", "contenido largo " * 10))
    _mem.documents.append(("nota_tmp", "x"))
    yield
    _mem.documents.clear()


@pytest.mark.asyncio
async def test_list_keys_devuelve_claves_y_tamanos(seeded):
    keys = await list_keys()
    names = [k["key"] for k in keys]
    assert "perfil_usuario" in names and "nota_tmp" in names
    assert all("chars" in k for k in keys)


@pytest.mark.asyncio
async def test_read_key_valor_y_inexistente(seeded):
    assert "contenido largo" in (await read_key("perfil_usuario") or "")
    assert (await read_key("no_esta")) is None


@pytest.mark.asyncio
async def test_delete_key_con_confirm(seeded):
    assert await delete_key("nota_tmp") is True
    assert all(k["key"] != "nota_tmp" for k in await list_keys())


def test_tab_muestra_claves_y_detalle(seeded):
    import asyncio

    from PySide6.QtWidgets import QApplication

    from desktop.memoria_tab import MemoriaTab

    _qapp = cast(QApplication, QApplication.instance() or QApplication([]))
    assert _qapp is not None
    with patch("desktop.memoria_tab.run_async") as ra:
        # run_async se ejecuta inline para el smoke (no hay loop en tests)
        ra.side_effect = lambda coro: asyncio.run(coro)
        tab = MemoriaTab()
        assert tab.keys_list.count() == 2
        tab.keys_list.setCurrentRow(0)
        assert "contenido largo" in tab.detail_view.toPlainText()
        assert tab.delete_btn.isEnabled()


def test_tab_refresca_al_cambiar_workspace(seeded):
    """El tab muestra memoria del workspace ACTIVO: sin este wiring quedaba
    stale tras un switch (mostraba claves del workspace anterior)."""
    import asyncio

    from PySide6.QtWidgets import QApplication

    from desktop.events import get_signals
    from desktop.memoria_tab import MemoriaTab

    _qapp = cast(QApplication, QApplication.instance() or QApplication([]))
    assert _qapp is not None
    with patch("desktop.memoria_tab.run_async") as ra:
        ra.side_effect = lambda coro: asyncio.run(coro)
        tab = MemoriaTab()
        assert tab.keys_list.count() == 2
        _mem.documents.clear()
        _mem.documents.append(("clave_del_nuevo_ws", "datos nuevos"))
        get_signals().workspace_changed.emit("otro")
        assert tab.keys_list.count() == 1
        assert tab.keys_list.item(0).text().startswith("clave_del_nuevo_ws")
