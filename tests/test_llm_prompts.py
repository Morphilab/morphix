"""Tests for llm/prompts.py — get_prompt, templates."""

import pytest

from llm.prompts import (
    ANTI_FRUSTRATION_PROMPT,
    DECOMPOSE_TASK_PROMPT,
    PLAN_VERIFY_PROMPT,
    VERIFY_GLOBAL_PROMPT,
    get_prompt,
)


class TestGetPrompt:
    def test_decompose_with_kwargs(self):
        result = get_prompt("decompose", query="build an API", project_context="")
        assert "build an API" in result
        assert "{query}" not in result

    def test_anti_frustration_without_kwargs(self):
        result = get_prompt("anti_frustration")
        assert "Morphix" in result
        assert "Reglas anti-frustración" in result

    def test_missing_prompt_raises_keyerror(self):
        with pytest.raises(KeyError):
            get_prompt("nonexistent")

    def test_all_templates_non_empty(self):
        assert len(DECOMPOSE_TASK_PROMPT) > 0
        assert len(ANTI_FRUSTRATION_PROMPT) > 0
        assert len(PLAN_VERIFY_PROMPT) > 0
        assert len(VERIFY_GLOBAL_PROMPT) > 0
