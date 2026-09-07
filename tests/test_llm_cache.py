# tests/test_llm_cache.py
"""Tests del sistema de caching LLM absorbido de deepseek-harness.

Cubre: persistencia atómica de stats por workspace, epoch de call-config
(invalidación conservadora) y compresión cache-first (prefijo estable).
"""

import json
from unittest.mock import patch

from core.cache_manager import CacheManager, CacheStats


def _fresh_manager(tmp_path):
    """Instancia aislada del manager (el singleton global no se toca)."""
    cm = object.__new__(CacheManager)
    cm._init(stats_path=tmp_path / "llm_cache_stats.json")
    return cm


# ── Persistencia ────────────────────────────────────────────────────────


class TestPersistence:
    def test_first_usage_throttled_then_manual_flush(self, tmp_path):
        cm = _fresh_manager(tmp_path)
        cm.track_usage(
            prompt_tokens=100,
            completion_tokens=40,
            prompt_cache_hit_tokens=60,
            prompt_cache_miss_tokens=40,
            workspace="w1",
            uncached_input_tokens=40,
            cache_write_tokens=40,
        )
        # El throttle no disparó aún: sin archivo tras el primer uso
        assert not (tmp_path / "llm_cache_stats.json").exists()
        # Flush manual y verificación del payload atómico completo
        cm.flush_to_disk()
        payload = json.loads((tmp_path / "llm_cache_stats.json").read_text())
        assert payload["version"] == 1
        assert payload["workspaces"]["w1"]["cache_hit_tokens"] == 60
        assert payload["workspaces"]["w1"]["uncached_input_tokens"] == 40
        assert payload["global"]["llm_calls"] == 1

    def test_flush_after_interval_persists_and_reload_restores(self, tmp_path):
        import core.cache_manager as cache_mod

        cm = _fresh_manager(tmp_path)
        with patch.object(cache_mod, "_FLUSH_INTERVAL_SECONDS", 0.0):
            cm.track_usage(prompt_tokens=50, completion_tokens=20, workspace="w1")
            cm.track_usage(prompt_tokens=10, completion_tokens=5, workspace="w2")

        stats_file = tmp_path / "llm_cache_stats.json"
        assert stats_file.exists(), "con intervalo 0 cada track debe persistir"
        payload = json.loads(stats_file.read_text())
        assert set(payload["workspaces"]) == {"w1", "w2"}

        # Reload en una instancia nueva: estadísticas restauradas
        cm2 = _fresh_manager(tmp_path)
        assert cm2._workspace_stats["w1"].total_prompt_tokens == 50
        assert cm2._workspace_stats["w2"].total_prompt_tokens == 10

    def test_corrupt_file_tolerated(self, tmp_path):
        stats_file = tmp_path / "llm_cache_stats.json"
        stats_file.write_text("{not json")
        cm = _fresh_manager(tmp_path)  # no debe lanzar
        assert cm._workspace_stats == {}
        cm.track_usage(prompt_tokens=1, workspace="w9")
        cm.flush_to_disk()
        assert (
            json.loads((tmp_path / "llm_cache_stats.json").read_text())["workspaces"]["w9"][
                "total_prompt_tokens"
            ]
            == 1
        )

    def test_epoch_fields_roundtrip(self, tmp_path):
        cm = _fresh_manager(tmp_path)
        cm.note_epoch("ws", "deepseek", "deepseek-v4-flash", 8192)
        cm.flush_to_disk()
        cm2 = _fresh_manager(tmp_path)
        st = cm2._workspace_stats.get("ws") or CacheStats()
        assert st.last_epoch != ""  # restaurado desde disco

    def test_atomic_write_no_temp_left(self, tmp_path):
        cm = _fresh_manager(tmp_path)
        cm.track_usage(prompt_tokens=1, workspace="atomic_ws")
        cm.flush_to_disk()
        assert not list(tmp_path.glob("*.tmp"))


# ── Epoch de call-config ─────────────────────────────────────────────────


class TestEpoch:
    def test_same_config_no_invalidation(self, tmp_path):
        cm = _fresh_manager(tmp_path)
        cm.note_epoch("ws", "deepseek", "model-x", 1024)
        cm.note_epoch("ws", "deepseek", "model-x", 1024)
        stats = cm.get_stats("ws")
        assert stats["epoch_changes"] == 0
        assert stats["last_epoch"] != ""

    def test_changed_config_counts_invalidation(self, tmp_path, caplog):
        cm = _fresh_manager(tmp_path)
        e1 = cm.note_epoch("ws", "deepseek", "model-x", 1024)
        with caplog.at_level("WARNING"):
            e2 = cm.note_epoch("ws", "deepseek", "model-y", 1024)
        assert e1 != e2
        assert cm.get_stats("ws")["epoch_changes"] == 1
        assert any("invalidada" in r.message.lower() for r in caplog.records)

    def test_max_tokens_change_is_epoch_level(self, tmp_path):
        cm = _fresh_manager(tmp_path)
        cm.note_epoch("ws", "p", "m", 1024)
        cm.note_epoch("ws", "p", "m", 2048)
        assert cm.get_stats("ws")["epoch_changes"] == 1


# ── Compresión cache-first (prefijo estable) ────────────────────────────


def _long_history():
    msgs = [{"role": "system", "content": "SYSTEM-PROMPT-FIJO"}]
    for i in range(12):
        msgs.append({"role": "user", "content": f"pregunta {i} " + "x" * 200})
        msgs.append({"role": "assistant", "content": f"respuesta {i} " + "y" * 200})
    return msgs


class TestCacheFriendlyCompress:
    def test_preserves_prefix_when_flag_on(self, tmp_path):

        history = _long_history()
        result = CacheManager.cache_friendly_compress(history, max_tokens=1200)
        assert result[0]["content"] == "SYSTEM-PROMPT-FIJO", "system debe encabezar el prefijo"

    def test_falls_back_to_compress_when_flag_off(self, monkeypatch):
        from core.config import settings as app_settings
        from core.context_manager import ContextManager

        history = _long_history()
        monkeypatch.setattr(app_settings, "prompt_cache_preserve_prefix", False)
        result_off = CacheManager.cache_friendly_compress(history, max_tokens=1200)
        expected = ContextManager.compress_history(history, max_tokens=1200)
        assert result_off == expected, "con flag off debe delegar en compress_history clásico"

        # Y con flag activo (por defecto) usa el estabilizador de prefijo
        result_on = CacheManager.cache_friendly_compress(history, max_tokens=1200)
        assert result_on == CacheManager.stabilize_messages(history, max_tokens=1200)

    def test_short_history_passes_through_stabilizer(self):
        short = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        result = CacheManager.cache_friendly_compress(short, max_tokens=8000)
        assert result == short


# ── Doble contabilización en _track_usage ───────────────────────────────


def test_track_usage_cuenta_una_sola_vez_por_respuesta(tmp_path, monkeypatch):
    """track_usage se llamaba DOS veces (con y sin buckets
    disjuntos) → llm_calls y tokens ×2 en CacheManager."""
    from types import SimpleNamespace

    import llm.controller as ctrl_mod

    cm = _fresh_manager(tmp_path)
    # _track_usage importa el singleton DENTRO de la función: parchear fuente
    monkeypatch.setattr("core.cache_manager.cache_manager", cm)
    from core import metrics as _metrics

    for fn in ("record_llm_usage", "record_disjoint_usage", "record_llm_latency"):
        monkeypatch.setattr(_metrics.metrics, fn, lambda *a, **k: None, raising=False)

    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        prompt_cache_hit_tokens=60,
        prompt_cache_miss_tokens=40,
    )
    ctrl_mod.ModelsController._track_usage(
        SimpleNamespace(usage=usage),
        workspace="ws_m1",
        provider="deepseek",
        model="m1",
        max_tokens=1024,
    )

    stats = cm.get_stats("ws_m1")
    assert stats["llm_calls"] == 1, "DSH2-M1: la respuesta se contó más de una vez"
    assert stats["prompt_tokens_total"] == 100
    assert stats["completion_tokens_total"] == 50
    assert stats["cache_hit_tokens"] == 60
    assert stats["cache_miss_tokens"] == 40
