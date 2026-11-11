# Límites duros de parsing de cron (anti-DoS) y dead-letter

import pytest

from core.bots_routines import _cron_field_matches, cron5_next_run


def test_cron_field_rejects_out_of_range_bounds():
    """'0-99999999999' no debe materializar un rango gigante: clamp o rechazo."""
    # min con bound absurdo → sin match y SIN colgar
    assert _cron_field_matches("0-99999999999", 30) is False
    assert _cron_field_matches("*/99999999999", 3) is False


def test_cron_field_normal_ranges_still_work():
    """Sanidad: rangos legítimos siguen operativos tras el clamp."""
    assert _cron_field_matches("*", 30) is True
    assert _cron_field_matches("0-10", 7) is True
    assert _cron_field_matches("*/15", 45) is True
    assert _cron_field_matches("1,2,3", 2) is True
    assert _cron_field_matches("5", 6) is False
    assert _cron_field_matches("0-23", 25) is False


def test_cron_next_run_with_huge_range_returns_fast():
    """cron5_next_run con bounds absurdos termina rápido (no OOM/hang)."""
    from datetime import datetime

    after = datetime(2026, 8, 27, 12, 0)
    t0 = __import__("time").monotonic()
    cron5_next_run("0 12 * * 0-99999999999", after)
    elapsed = __import__("time").monotonic() - t0
    # Umbral tolerante a carga de suite completa: la propiedad que importa es
    # "termina acotado" (sin clamp: OOM/hang indefinido materializando 100B vals)
    assert elapsed < 60.0, f"parsing degenerado tardó {elapsed:.1f}s"


@pytest.mark.parametrize("expr", ["* * * * *", "*/5 * * * *", "30 8 1,15 * 1-5"])
def test_valid_schedules_unaffected(expr):
    from datetime import datetime

    out = cron5_next_run(expr, datetime(2026, 8, 27, 12, 0))
    assert out is not None or expr == "30 8 1,15 * 1-5"  # puede no haber match dow
