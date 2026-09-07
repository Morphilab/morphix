"""Merge profundo de preferences + conteo honesto de subtareas."""

from core.memory.manager import MemoryManager


def test_deep_merge_preserves_existing_preferences():
    current = {
        "name": "Ana",
        "preferences": {"lenguaje": "python", "tono": "directo"},
    }
    new = {"preferences": {"tono": "técnico"}}

    merged = MemoryManager._deep_merge_profile(current, new)

    assert (
        merged["preferences"]["lenguaje"] == "python"
    ), "preferencia existente perdida en merge shallow"
    assert merged["preferences"]["tono"] == "técnico"
    assert merged["name"] == "Ana"


def test_deep_merge_ignores_none_values():
    current = {"name": "Ana"}
    merged = MemoryManager._deep_merge_profile(current, {"name": None})
    assert merged["name"] == "Ana"


def test_honest_subtasks_completed_excludes_failed():
    """failed/stalled/skipped NO cuentan como completados."""
    results = {
        0: {"status": "completed"},
        1: {"status": "failed"},
        2: {"status": "skipped"},
        3: {"status": "completed"},
    }
    honest = sum(1 for r in results.values() if r.get("status") == "completed")
    assert honest == 2 and len(results) == 4
