# tests/test_cache_manager.py — ruido del prompt-cache
"""note_epoch: alternar agent↔fast es el patrón normal de un turno de bot
(finalizer) — cada alternancia generaba un WARNING. Una config ya vista en
la ventana reciente baja a DEBUG; solo una config genuinamente nueva advierte."""

import logging

from core.cache_manager import CacheManager


def _fresh(tmp_path):
    cm = object.__new__(CacheManager)
    cm._init(stats_path=tmp_path / "stats.json")
    return cm


def test_note_epoch_alternating_config_does_not_warn(tmp_path, caplog):
    cm = _fresh(tmp_path)
    with caplog.at_level("WARNING", logger="core.cache_manager"):
        e_fast = cm.note_epoch("main", "deepseek", "m-fast", 100)
        e_agent = cm.note_epoch("main", "deepseek", "m-agent", 100)
        assert e_fast != e_agent
        for _ in range(3):
            cm.note_epoch("main", "deepseek", "m-fast", 100)
            cm.note_epoch("main", "deepseek", "m-agent", 100)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert (
        len(warnings) == 1
    ), f"solo la primera vista de la 2ª config advierte: {[r.getMessage() for r in warnings]}"


def test_note_epoch_alternating_config_traceable_at_debug(tmp_path, caplog):
    """El patrón alternante no se borra: queda a nivel DEBUG para diagnóstico."""
    cm = _fresh(tmp_path)
    cm.note_epoch("main", "deepseek", "m-fast", 100)
    cm.note_epoch("main", "deepseek", "m-agent", 100)
    with caplog.at_level("DEBUG", logger="core.cache_manager"):
        cm.note_epoch("main", "deepseek", "m-fast", 100)
        cm.note_epoch("main", "deepseek", "m-agent", 100)
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debugs, "el patrón alternante debe quedar trazable a nivel DEBUG"
    assert any("alternante" in r.getMessage() for r in debugs)


def test_note_epoch_warns_on_genuinely_new_config(tmp_path, caplog):
    cm = _fresh(tmp_path)
    cm.note_epoch("main", "deepseek", "m-fast", 100)
    cm.note_epoch("main", "deepseek", "m-agent", 100)

    with caplog.at_level("WARNING", logger="core.cache_manager"):
        cm.note_epoch("main", "ollama", "otro-modelo", 200)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("call-config" in r.message for r in warnings), warnings


def test_note_epoch_counts_changes_honestly(tmp_path):
    """Las métricas epoch_changes NO mienten: cuentan cada transición aunque el
    log baje a DEBUG (solo cambia el nivel de ruido, no la señal)."""
    cm = _fresh(tmp_path)
    cm.note_epoch("main", "deepseek", "m-fast", 100)
    cm.note_epoch("main", "deepseek", "m-agent", 100)
    cm.note_epoch("main", "deepseek", "m-fast", 100)
    cm.note_epoch("main", "deepseek", "m-agent", 100)
    assert cm._workspace_stats["main"].epoch_changes == 3
    assert cm._global_stats.epoch_changes == 3
