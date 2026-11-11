"""Tests for FrustrationDetector — pattern matching, calming prompts, reset."""

from core.security.frustration_detector import FrustrationDetector


class TestFrustrationDetector:
    def setup_method(self):
        fd = FrustrationDetector()
        fd.reset()

    def test_no_frustration(self):
        fd = FrustrationDetector()
        is_frustrated, reason = fd.check("hello world")
        assert is_frustrated is False
        assert reason is None

    def test_swearing_detected(self):
        fd = FrustrationDetector()
        is_frustrated, reason = fd.check("this is stupid and broken")
        assert is_frustrated is True
        assert reason in ("swearing", "frustration_signal")

    def test_shouting_detected(self):
        fd = FrustrationDetector()
        is_frustrated, reason = fd.check("WHY IS THIS NOT WORKING")
        assert is_frustrated is True
        assert reason == "shouting"

    def test_continue_spam(self):
        fd = FrustrationDetector()
        is_frustrated, reason = fd.check("continue")
        assert is_frustrated is True
        assert reason == "continue_spam"

    def test_repeated_identical_query(self):
        fd = FrustrationDetector()
        fd.check("fix the bug")
        fd.check("fix the bug")
        assert fd.frustration_count == 0
        is_frustrated, reason = fd.check("fix the bug")
        assert is_frustrated is True
        assert reason == "repeated_identical_query"

    def test_empty_query_no_frustration(self):
        fd = FrustrationDetector()
        is_frustrated, _ = fd.check("")
        assert is_frustrated is False

    def test_increments_frustration_count(self):
        fd = FrustrationDetector()
        fd.check("this is stupid")
        assert fd.frustration_count == 1
        fd.check("damn it")
        assert fd.frustration_count == 2

    def test_get_calming_prompt_level_0(self):
        fd = FrustrationDetector()
        assert fd.get_calming_prompt() == ""

    def test_get_calming_prompt_level_1(self):
        fd = FrustrationDetector()
        fd.check("this is stupid")
        prompt = fd.get_calming_prompt()
        assert "frustrated" in prompt.lower()
        assert "stay calm" in prompt.lower()

    def test_get_calming_prompt_level_3(self):
        fd = FrustrationDetector()
        fd.check("stupid")
        fd.check("fuck")
        fd.check("this is useless")
        prompt = fd.get_calming_prompt()
        assert "extra patient" in prompt.lower()

    def test_reset_clears_state(self):
        fd = FrustrationDetector()
        fd.check("stupid")
        fd.check("this is useless")
        assert fd.frustration_count == 2
        fd.reset()
        assert fd.frustration_count == 0
        assert fd.last_frustrated_at is None
        assert fd._recent_queries == []
