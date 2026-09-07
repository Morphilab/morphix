# tests/test_multisession_switch_guard.py
"""Multi-sesión: el switch guard es multi-observador, no slot único.

Con dos panes Maestro cada uno registra su veto; el switch de workspace debe
bloquearse si CUALQUIERA de ellos veta, sin que un registro sobrescriba al
anterior.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_multiple_guards_any_veto_blocks_switch():
    from core.workspaces import Workspaces

    mgr = Workspaces()
    mgr.add_switch_guard(lambda name: True)
    mgr.add_switch_guard(lambda name: False)  # este pane veta

    with patch.object(mgr, "_do_switch_workspace", new=AsyncMock()) as mock_do:
        ok = await mgr.switch_workspace("destino")

    assert ok is False
    mock_do.assert_not_awaited()


@pytest.mark.asyncio
async def test_removing_vetoing_guard_unblocks_switch():
    from core.workspaces import Workspaces

    mgr = Workspaces()
    token = mgr.add_switch_guard(lambda name: False)
    mgr.remove_switch_guard(token)

    async def fake_do(name, retries):
        mgr.current = name
        return True

    with patch.object(mgr, "_do_switch_workspace", side_effect=fake_do):
        ok = await mgr.switch_workspace("destino")

    assert ok is True
    assert mgr.current == "destino"


@pytest.mark.asyncio
async def test_all_guards_permit_allows_switch():
    from core.workspaces import Workspaces

    mgr = Workspaces()
    mgr.add_switch_guard(lambda name: True)
    mgr.add_switch_guard(lambda name: True)

    async def fake_do(name, retries):
        mgr.current = name
        return True

    with patch.object(mgr, "_do_switch_workspace", side_effect=fake_do):
        ok = await mgr.switch_workspace("destino")

    assert ok is True
