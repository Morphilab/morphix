# tests/test_bots_tab_smoke.py — pestaña Bots: wiring Qt offscreen
"""Smoke de la pestaña Bots sin display (QT_QPA_PLATFORM=offscreen).

Verifica construcción, registro de señal y roster desde la resolución
canónica server-side — sin tocar MainWindow completo.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import secrets  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")

from datetime import datetime  # noqa: E402

from core.bots import BotsService  # noqa: E402
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
)  # noqa: E402
from desktop.bots_tab import BotsTab, _fmt_local_schedule  # noqa: E402


def _url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.mark.skipif(not _url(), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_bots_tab_lists_and_emits_open_conversation():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    sch = f"bots_tab_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            bot = await BotsService.create_bot("glow", display_name="Glow")

            tab = BotsTab()
            captured: list[int] = []
            tab.open_conversation.connect(captured.append)

            # El __init__ ya lanzó refresh vía run_async; esperamos al future.
            import asyncio

            for _ in range(100):
                if tab.roster.list_widget.count() >= 1:
                    break
                await asyncio.sleep(0.05)
            assert tab.roster.list_widget.count() == 1
            item = tab.roster.list_widget.item(0)
            assert "● @glow" in item.text() or "@glow" in item.text()

            tab.roster.list_widget.setCurrentItem(item)  # un clic real fija el current
            row = tab.roster.current_row()
            assert row and row["slug"] == "glow"

            # Clic de apertura end-to-end por el bridge async real
            tab.roster._on_open_clicked()
            for _ in range(200):
                if captured:
                    break
                app.processEvents()
                await asyncio.sleep(0.02)
            assert captured, "open_conversation debió emitir tras ensure_open"
            assert isinstance(captured[0], int)
    finally:
        await drop_schema(sch)


# ── Rediseño v2: contenedor con 3 sub-pestañas ────────────────


@pytest.mark.asyncio
async def test_bots_tab_container_has_three_subtabs(monkeypatch):
    """Contrato rediseño: BotsTab es contenedor con inner_tabs de 3 sub-pestañas."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "messages_of", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt, "list_routines", AsyncMock(return_value=[]))

    tab = BotsTab()
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            if tab.roster._rows == [] and tab.groups_pane.rooms_combo.count() == 0:
                break
        labels = [tab.inner_tabs.tabText(i) for i in range(tab.inner_tabs.count())]
        assert labels == ["Bots", "Rutinas", "Grupos"]
        assert tab.inner_tabs.widget(0) is tab.roster
        assert tab.inner_tabs.widget(1) is tab.routines_pane
        assert tab.inner_tabs.widget(2) is tab.groups_pane
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


# ── el roster es por-workspace — refresh en workspace_changed ─────────


@pytest.mark.asyncio
async def test_bots_tab_refreshes_roster_on_workspace_change(monkeypatch):
    """Tras un switch, la lista NO debe seguir mostrando
    bots del schema anterior (abrir su chat falla: 'no existe el bot').
    Contrato: workspace_changed invalida filas y recarga el roster."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop.events import get_signals
    from desktop.services import bots_service as bs

    roster = AsyncMock(return_value=[{"slug": "glow", "display_name": "Glow", "canonical": None}])
    monkeypatch.setattr(bs, "roster_with_previews", roster)

    tab = BotsTab()
    try:
        for _ in range(100):
            if tab.roster.list_widget.count() == 1 and roster.await_count >= 1:
                break
            await asyncio.sleep(0.05)
        assert tab.roster.list_widget.count() == 1

        get_signals().workspace_changed.emit("otro_ws")
        for _ in range(200):
            if roster.await_count >= 2 and tab.roster.list_widget.count() == 1:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert roster.await_count >= 2, "el switch debió disparar un refresh del roster"
        assert tab.roster.list_widget.count() == 1
        assert tab.roster.current_row() is None or tab.roster.current_row()["slug"] == "glow"
    finally:
        try:
            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


# ── Grupos: selector de salas + visor del log ────────────────


@pytest.mark.asyncio
async def test_groups_pane_log_uses_morphix_log_style(monkeypatch):
    """Contrato: room_log es QTextBrowser readonly con StyleFactory.text_browser_log()."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication, QTextBrowser

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs
    from desktop.theme import StyleFactory

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "messages_of", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt, "list_routines", AsyncMock(return_value=[]))

    tab = BotsTab()
    try:
        pane = tab.groups_pane
        for _ in range(100):
            if pane._rooms_loaded:
                break
            await asyncio.sleep(0.02)
        log = pane.room_log
        assert isinstance(log, QTextBrowser)
        assert log.isReadOnly()
        assert log.styleSheet() == StyleFactory.text_browser_log()
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


@pytest.mark.asyncio
async def test_bots_tab_lists_rooms_and_loads_log(monkeypatch):
    """La GUI debe listar salas existentes y mostrar
    su log (no solo vivir en BD; `_active_room` se pierde al reiniciar).
    Contrato: refresh() puebla el combo desde list_rooms() y cambiar de sala
    carga su log desde messages_of()."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    rooms = [
        {"id": "r1-abc", "name": "coord", "owner_bot_slug": "nord", "members": ["nord", "sud"]},
        {"id": "r2-def", "name": "legal", "owner_bot_slug": "sud", "members": ["nord", "sud"]},
    ]

    def _log_for(room_id, limit=100):
        if room_id == "r1-abc":
            return [
                {"author": "user", "content": "@sud resume", "seq": 1},
                {"author": "sud", "content": "resumen ok", "seq": 2},
            ]
        return [{"author": "nord", "content": "otra sala", "seq": 1}]

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=rooms))
    messages_of = AsyncMock(side_effect=_log_for)
    monkeypatch.setattr(bt.groups_mod, "messages_of", messages_of)

    tab = BotsTab()
    try:
        pane = tab.groups_pane
        for _ in range(200):
            if pane.rooms_combo.count() == 2 and messages_of.await_count >= 1:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert pane.rooms_combo.count() == 2
        # primera sala (índice 0) seleccionada por defecto y su log cargado
        assert pane._current_room_id() == "r1-abc"
        assert "@sud: resumen ok" in pane.room_log.toPlainText()

        pane.rooms_combo.setCurrentIndex(1)  # cambia de sala → recarga el log
        for _ in range(200):
            if messages_of.await_count >= 2:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert messages_of.await_count >= 2, "cambiar de sala debe recargar el log"
        assert pane._current_room_id() == "r2-def", "la sala activa se deriva del combo"
        assert "otra sala" in pane.room_log.toPlainText()
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


@pytest.mark.asyncio
async def test_bots_tab_post_group_uses_combo_room(monkeypatch):
    """El post a un grupo usa la sala seleccionada en el combo (no solo la
    creada en sesión) y refresca el log + el estado de la ronda."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bt.groups_mod,
        "list_rooms",
        AsyncMock(
            return_value=[
                {"id": "r9-x", "name": "sala", "owner_bot_slug": "a", "members": ["a", "b"]}
            ]
        ),
    )
    messages_of = AsyncMock(return_value=[{"author": "a", "content": "hola", "seq": 1}])
    monkeypatch.setattr(bt.groups_mod, "messages_of", messages_of)
    run_room_turn = AsyncMock(
        return_value={
            "status": "round-complete",
            "responders": ["a", "b"],
            "passes": 1,
            "stranded": [],
        }
    )
    import orchestration.bots_groups_drive as gd

    monkeypatch.setattr(gd, "run_room_turn", run_room_turn)

    tab = BotsTab()
    try:
        pane = tab.groups_pane
        for _ in range(200):
            if pane.rooms_combo.count() == 1:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert pane._current_room_id() == "r9-x"

        res = await pane._post_to_room("r9-x", "hola a todos")
        assert res and res["status"] == "round-complete"
        run_room_turn.assert_awaited_once_with("r9-x", "hola a todos")
        assert "@a: hola" in pane.room_log.toPlainText()
        assert "respondieron a, b" in pane.status.text()
        assert "1 pass" in pane.status.text()
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


# ── Rutinas: administración por selección ─────────────────────


@pytest.mark.asyncio
async def test_routines_pane_lists_rows_with_status_colors(monkeypatch):
    """Contrato: rutinas en QListWidget; ▶ success / ⏸ warning / ⚠ error visible."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs
    from desktop.theme import COLORS

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bt,
        "list_routines",
        AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "name": "prueba",
                    "schedule": "1m",
                    "slug": "alfa",
                    "enabled": True,
                    "next_run_at": "2026-08-29 12:00:00",
                    "last_error": None,
                },
                {
                    "id": 2,
                    "name": "otra",
                    "schedule": "30m",
                    "slug": None,
                    "enabled": False,
                    "next_run_at": None,
                    "last_error": "boom",
                },
            ]
        ),
    )

    tab = BotsTab()
    try:
        pane = tab.routines_pane
        for _ in range(200):
            if pane.routines_list.count() == 2:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert pane.routines_list.count() == 2
        it0, it1 = pane.routines_list.item(0), pane.routines_list.item(1)
        assert "▶" in it0.text() and "prueba" in it0.text()
        assert f"próxima {_fmt_local_schedule(datetime(2026, 8, 29, 12, 0))}" in it0.text()
        assert it0.foreground().color().name() == QColor(COLORS["success"]).name()
        assert "⏸" in it1.text() and "⚠ boom" in it1.text()
        assert it1.foreground().color().name() == QColor(COLORS["warning"]).name()
        pane.routines_list.setCurrentItem(it0)
        assert pane._current_routine_id() == 1
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


@pytest.mark.asyncio
async def test_bots_tab_routines_admin_by_selection(monkeypatch):
    """toggle/delete NO deben exigir adivinar el id por diálogo.
    Contrato: el combo lista las rutinas con estado y las acciones usan la
    selección (sin diálogos de id); el estado muestra próxima corrida/error."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    routines = [
        {
            "id": 1,
            "name": "prueba",
            "schedule": "1m",
            "slug": "alfa",
            "enabled": True,
            "next_run_at": "2026-08-29 12:00:00",
            "last_error": None,
        },
        {
            "id": 2,
            "name": "otra",
            "schedule": "30m",
            "slug": None,
            "enabled": False,
            "next_run_at": None,
            "last_error": "boom",
        },
    ]
    monkeypatch.setattr(bt, "list_routines", AsyncMock(return_value=routines))
    set_en = AsyncMock(return_value=True)
    monkeypatch.setattr(bt, "set_routine_enabled", set_en)
    import core.bots_routines as cr

    del_rt = AsyncMock(return_value=True)
    monkeypatch.setattr(cr, "delete_routine", del_rt)

    tab = BotsTab()
    try:
        pane = tab.routines_pane
        for _ in range(200):
            if pane.routines_list.count() == 2:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert pane.routines_list.count() == 2
        assert "prueba" in pane.routines_list.item(0).text()
        pane.routines_list.setCurrentItem(pane.routines_list.item(0))
        assert pane._current_routine_id() == 1

        await pane._toggle_routine(1)
        set_en.assert_awaited_once_with(1, False)  # flip enabled True→False

        await pane._delete_routine(2)
        del_rt.assert_awaited_once_with(2)

        await pane._status_for_routine(2)
        assert "⚠ falló: boom" in pane.status.text(), "el error de la rutina debe mostrarse"
        await pane._status_for_routine(1)
        assert f"próxima {_fmt_local_schedule(datetime(2026, 8, 29, 12, 0))}" in pane.status.text()
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


@pytest.mark.asyncio
async def test_bots_tab_create_routine_maps_delivery(monkeypatch):
    """El helper de creación recibe el modo de entrega explícito (no derivado
    del clic del roster) y selecciona la rutina recién creada en el combo."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    created = {"id": 9, "name": "nueva", "schedule": "1m", "enabled": True}
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(bt, "create_routine", create)
    monkeypatch.setattr(
        bt,
        "list_routines",
        AsyncMock(
            return_value=[{**created, "slug": None, "next_run_at": None, "last_error": None}]
        ),
    )

    tab = BotsTab()
    try:
        pane = tab.routines_pane
        for _ in range(200):
            if pane.routines_list.count() == 1:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert pane.routines_list.count() == 1
        await pane._create_routine("nueva", "1m", "prompt", None, "history")
        create.assert_awaited_once_with(
            "nueva", "1m", prompt="prompt", slug=None, deliver="history"
        )
        assert pane._current_routine_id() == 9

        await pane._create_routine("para-bot", "2m", "prompt2", "alfa", "bot-chat")
        assert create.await_count == 2
        assert create.await_args.kwargs["slug"] == "alfa"
        assert create.await_args.kwargs["deliver"] == "bot-chat"
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


@pytest.mark.asyncio
async def test_bots_tab_roster_toggle_deselect(monkeypatch):
    """Debe existir forma de deseleccionar un bot (un clic
    en Qt no deselecciona). Contrato: re-clic sobre el seleccionado lo
    suelta; desacoplado de rutinas (mensajes sin 'bot-chat'/'history')."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "messages_of", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt, "list_routines", AsyncMock(return_value=[]))

    tab = BotsTab()
    try:
        pane = tab.roster
        for _ in range(100):
            if pane.list_widget.count() == 0 and tab.groups_pane.rooms_combo.count() == 0:
                break
            await asyncio.sleep(0.01)
        for slug in ("alfa", "beta"):
            it = QListWidgetItem(f"@ {slug}")
            it.setData(Qt.ItemDataRole.UserRole, {"slug": slug})
            pane.list_widget.addItem(it)

        pane.list_widget.setCurrentItem(pane.list_widget.item(0))
        pane._on_roster_clicked(pane.list_widget.item(0))
        assert pane._selected_slug() == "alfa"
        assert "seleccionado @alfa" in pane.status.text()

        # re-clic sobre el mismo bot → deselecciona
        pane._on_roster_clicked(pane.list_widget.item(0))
        assert pane._selected_slug() is None, "re-clic debe deseleccionar"

        pane.list_widget.setCurrentItem(pane.list_widget.item(1))
        pane._on_roster_clicked(pane.list_widget.item(1))
        assert pane._selected_slug() == "beta"
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


@pytest.mark.asyncio
async def test_bots_tab_room_memory_text_formats(monkeypatch):
    """La memoria por sala de cada bot debe ser visible desde la GUI. Contrato:
    _room_memory_text formatea la transcripción 'Group: <rid>' por miembro."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "messages_of", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt, "list_routines", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bt.groups_mod,
        "room_member_transcripts",
        AsyncMock(
            return_value={
                "ana": [
                    {"role": "user", "content": "[mem] hola"},
                    {"role": "assistant", "content": "hola ana"},
                ],
                "bo": [
                    {"role": "user", "content": "[mem] hola"},
                    {"role": "assistant", "content": "hola bo"},
                ],
            }
        ),
    )

    tab = BotsTab()
    try:
        pane = tab.groups_pane
        for _ in range(100):
            if pane.rooms_combo.count() == 0:
                break
            await asyncio.sleep(0.01)
        text = await pane._room_memory_text("r1-x")
        assert "── @ana ──" in text and "── @bo ──" in text
        assert "[bot] hola ana" in text and "[bot] hola bo" in text
        assert "[usuario] [mem] hola" in text

        # sin sesiones → mensaje accionable
        monkeypatch.setattr(bt.groups_mod, "room_member_transcripts", AsyncMock(return_value={}))
        assert "postea un mensaje primero" in await pane._room_memory_text("r1-x")
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


# ── la navegación del sidebar debe exponer la pestaña Bots ─────────


@pytest.mark.asyncio
async def test_option_a_one_primary_create_action_per_pane(monkeypatch):
    """Opción A: crear = primary_button (uno por sub-tab); ⟳ = secondary_button."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs
    from desktop.theme import StyleFactory

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "messages_of", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt, "list_routines", AsyncMock(return_value=[]))

    tab = BotsTab()
    try:
        for _ in range(100):
            if tab.groups_pane._rooms_loaded:
                break
            await asyncio.sleep(0.02)
        assert tab.roster.btn_new.styleSheet() == StyleFactory.primary_button()
        assert tab.roster.btn_refresh.styleSheet() == StyleFactory.secondary_button()
        assert tab.routines_pane.btn_rt_new.styleSheet() == StyleFactory.primary_button()
        assert tab.routines_pane.btn_rt_refresh.styleSheet() == StyleFactory.secondary_button()
        assert tab.groups_pane.btn_gr_new.styleSheet() == StyleFactory.primary_button()
        assert tab.groups_pane.btn_rooms_refresh.styleSheet() == StyleFactory.secondary_button()
    finally:
        try:
            from desktop.events import get_signals

            get_signals().workspace_changed.disconnect(tab._on_workspace_changed)
        except (RuntimeError, TypeError):
            pass


def test_sidebar_has_bots_entry_last_and_icons_exist():
    """Si _StackedShim.addTab añade 'Bots' al stack pero
    _SIDEBAR_ITEMS no recibe el item, la página queda inalcanzable desde la UI.

    Contrato: 'Bots' es el ÚLTIMO item del sidebar (alineado con el orden de
    addTab en _load_real_tabs) y todos los iconos referenciados existen."""
    from desktop.icons import AVAILABLE_ICONS
    from desktop.main_window import _SIDEBAR_ITEMS

    labels = [label for label, _ in _SIDEBAR_ITEMS]
    assert labels[-1] == "Bots", f"sidebar sin Bots al final: {labels}"
    assert len(labels) == 8, f"esperados 8 tabs (7 + Bots), hay {len(labels)}"
    missing = [icon for _, icon in _SIDEBAR_ITEMS if icon not in AVAILABLE_ICONS]
    assert not missing, f"iconos inexistentes en desktop/icons.py: {missing}"


def test_sidebar_items_align_with_load_real_tabs_order():
    """Invariante: _stacked y _sidebar se
    sincronizan POR ÍNDICE (currentRowChanged→setCurrentIndex). Toda página
    añadida en _load_real_tabs DEBE tener su item en _SIDEBAR_ITEMS en la
    misma posición — si no, la fila del sidebar abre OTRA tab y la nueva
    queda inalcanzable (ocurrió: 'Bots' abría 'Memoria')."""
    import inspect
    import re

    from desktop.main_window import _SIDEBAR_ITEMS, MainWindow

    src = inspect.getsource(MainWindow._load_real_tabs)
    add_labels = re.findall(r'addTab\([^,]+,\s*"([^"]+)"\)', src)
    sidebar_labels = [label for label, _ in _SIDEBAR_ITEMS]
    assert add_labels, "no se encontraron addTab en _load_real_tabs"
    assert sidebar_labels == add_labels, (
        f"desalineado sidebar↔stack: sidebar={sidebar_labels} addTab={add_labels} "
        "— cada addTab necesita su item en _SIDEBAR_ITEMS (misma posición)"
    )


def test_load_real_tabs_adds_bots_tab_matching_sidebar():
    """El addTab de Bots en _load_real_tabs sigue el orden del sidebar."""
    import inspect

    from desktop.main_window import MainWindow

    src = inspect.getsource(MainWindow._load_real_tabs)
    assert 'addTab(bots_tab, "Bots")' in src, "main_window debe registrar la pestaña Bots"
    assert src.index("addTab(DashboardTab()") < src.index(
        "addTab(bots_tab"
    ), "el orden de pestañas debe respetar el orden del sidebar"


async def test_routines_pane_muestra_dead_letter_y_fallos(monkeypatch):
    """🛑 dead-letter y ✕N fallos son visibles en
    la lista — el usuario no necesita logs para saber que una rutina falla."""
    import asyncio
    from unittest.mock import AsyncMock

    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from desktop import bots_tab as bt
    from desktop.services import bots_service as bs
    from desktop.theme import COLORS

    monkeypatch.setattr(bs, "roster_with_previews", AsyncMock(return_value=[]))
    monkeypatch.setattr(bt.groups_mod, "list_rooms", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bt,
        "list_routines",
        AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "name": "rota",
                    "schedule": "1m",
                    "slug": None,
                    "enabled": True,
                    "next_run_at": None,
                    "last_error": "RuntimeError: boom",
                    "failure_count": 7,
                },
                {
                    "id": 2,
                    "name": "tambalea",
                    "schedule": "5m",
                    "slug": None,
                    "enabled": True,
                    "next_run_at": "2026-09-03 12:00:00",
                    "last_error": None,
                    "failure_count": 2,
                },
            ]
        ),
    )

    tab = BotsTab()
    try:
        pane = tab.routines_pane
        for _ in range(200):
            if pane.routines_list.count() == 2:
                break
            app.processEvents()
            await asyncio.sleep(0.02)
        assert pane.routines_list.count() == 2
        it0, it1 = pane.routines_list.item(0), pane.routines_list.item(1)
        assert "🛑 dead-letter (7 fallos, re-intento diario)" in it0.text()
        assert it0.foreground().color().name() == QColor(COLORS["error"]).name()
        assert "✕2 fallos" in it1.text()
        assert "🛑" not in it1.text()
        assert it1.foreground().color().name() == QColor(COLORS["warning"]).name()
    finally:
        tab.deleteLater()
        await asyncio.sleep(0)
