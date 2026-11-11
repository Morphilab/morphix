"""Daemon headless: single-instance, once, dry-run y parada limpia."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core import daemon


@pytest.fixture()
def _mocks():
    with (
        patch("core.bootstrap.init_backend", new_callable=AsyncMock, return_value=True) as init,
        patch("tools.loader.load_global_tools") as tools,
        patch("core.daemon._acquire_instance_lock", new_callable=AsyncMock) as lock,
        patch("core.daemon._release_instance_lock", new_callable=AsyncMock) as unlock,
        patch("core.database.dispose_engine", new_callable=AsyncMock) as dispose,
        patch(
            "orchestration.bots_wake.drain_tick", new_callable=AsyncMock, return_value={}
        ) as wake,
        patch("core.bots_routines.scheduler_tick", new_callable=AsyncMock, return_value={}) as tick,
    ):
        lock.return_value = object()
        yield {
            "init": init,
            "tools": tools,
            "lock": lock,
            "unlock": unlock,
            "dispose": dispose,
            "wake": wake,
            "tick": tick,
        }


@pytest.mark.asyncio
async def test_once_corre_un_tick_y_libera_el_lock(_mocks):
    assert await daemon.run_daemon(workspace="main", once=True) == 0
    _mocks["wake"].assert_awaited_once()
    _mocks["tick"].assert_awaited_once()
    _mocks["unlock"].assert_awaited_once()
    _mocks["dispose"].assert_awaited_once()


@pytest.mark.asyncio
async def test_si_otra_instancia_corre_sale_limpio(_mocks):
    _mocks["lock"].return_value = None
    assert await daemon.run_daemon(once=True) == 0
    _mocks["wake"].assert_not_awaited()
    _mocks["unlock"].assert_not_awaited()
    _mocks["dispose"].assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_no_ejecuta_ticks(_mocks):
    assert await daemon.run_daemon(dry_run=True) == 0
    _mocks["wake"].assert_not_awaited()
    _mocks["tick"].assert_not_awaited()
    _mocks["unlock"].assert_awaited_once()


@pytest.mark.asyncio
async def test_init_fallido_no_arranca(_mocks):
    _mocks["init"].return_value = False
    assert await daemon.run_daemon(once=True) == 1
    _mocks["lock"].assert_not_awaited()


@pytest.mark.asyncio
async def test_max_seconds_para_y_detiene_los_daemons(_mocks):
    with (
        patch("core.bootstrap.start_daemons", new_callable=AsyncMock) as start,
        patch("core.bootstrap.register_daemon") as register,
        patch("core.bootstrap.stop_daemons", new_callable=AsyncMock) as stop,
        patch("orchestration.bots_wake.bots_wake_loop"),
        patch("orchestration.bots_clock.routines_loop"),
    ):
        assert await daemon.run_daemon(max_seconds=0.01) == 0
        start.assert_awaited_once()
        assert register.call_count == 2
        stop.assert_awaited_once()
    _mocks["unlock"].assert_awaited_once()


def test_setup_logging_usa_su_archivo_sin_tocar_el_root(tmp_path):
    """El daemon fuerza su log en SU proceso; el test restaura el root para no
    romper caplog/logging de los tests que corren después."""
    import logging

    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    try:
        target = daemon.setup_logging(str(tmp_path / "daemon.log"))
        assert target == tmp_path / "daemon.log"
        assert any(
            isinstance(h, logging.FileHandler) and str(target) in h.baseFilename
            for h in root.handlers
        )
    finally:
        root.handlers[:] = saved
        root.setLevel(saved_level)
