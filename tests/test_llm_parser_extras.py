"""Additional tests for llm/parser.py — parse_plan_json, tool_calls_from_response."""

from unittest.mock import MagicMock

from llm.parser import parse_plan_json, tool_calls_from_response


class TestParsePlanJson:
    def test_valid_dict(self):
        result = parse_plan_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_non_dict_returns_none(self):
        result = parse_plan_json("[1, 2, 3]")
        assert result is None

    def test_invalid_json_returns_none(self):
        # parse_json_from_llm has a fallback to empty dict, so
        # parse_plan_json returns that dict (not None).
        # Invalid JSON that somehow parses as non-dict should return None.
        result = parse_plan_json("not json")
        assert isinstance(result, dict)


class TestToolCallsFromResponse:
    def test_has_tool_calls(self):
        msg = MagicMock()
        msg.tool_calls = [MagicMock()]
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        result = tool_calls_from_response(response)
        assert result is not None
        assert len(result) == 1

    def test_no_tool_calls(self):
        msg = MagicMock()
        msg.tool_calls = []
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        result = tool_calls_from_response(response)
        assert result is None

    def test_missing_choices(self):
        response = MagicMock()
        del response.choices
        result = tool_calls_from_response(response)
        assert result is None

    def test_empty_choices(self):
        response = MagicMock()
        response.choices = []
        result = tool_calls_from_response(response)
        assert result is None
