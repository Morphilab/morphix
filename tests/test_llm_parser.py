# tests/test_llm_parser.py

from llm import extract_json_block, parse_json_from_llm, try_parse_json


def test_try_parse_json_valid():
    result, error = try_parse_json('{"key": "value"}')
    assert result == {"key": "value"}
    assert error is None


def test_try_parse_json_invalid():
    result, error = try_parse_json("no es json")
    assert result is None
    assert error is not None


def test_extract_json_block_with_language():
    text = '```json\n{"a": 1}\n```'
    result = extract_json_block(text)
    assert result == '{"a": 1}'


def test_extract_json_block_without_language():
    text = '```\n{"b": 2}\n```'
    result = extract_json_block(text)
    assert result == '{"b": 2}'


def test_extract_json_block_multiline():
    text = """
Aquí está el resultado:

```json
{
  "name": "test",
  "value": 42
}
```

Eso es todo.
"""
    result = extract_json_block(text)
    assert '"name": "test"' in result
    assert '"value": 42' in result


def test_extract_json_block_not_found():
    result = extract_json_block("sin json block")
    assert result is None


def test_parse_json_from_llm_direct():
    result = parse_json_from_llm('{"key": "direct"}')
    assert result == {"key": "direct"}


def test_parse_json_from_llm_markdown_block():
    text = '```json\n{"from": "block"}\n```'
    result = parse_json_from_llm(text)
    assert result == {"from": "block"}


def test_parse_json_from_llm_with_wrapper_text():
    text = 'El resultado es: {"status": "ok", "count": 5}. Espero que sirva.'
    result = parse_json_from_llm(text)
    assert "status" in result
    assert result["status"] == "ok"


def test_parse_json_from_llm_invalid_fallback():
    result = parse_json_from_llm("esto no es json para nada", default={"fallback": True})
    assert result == {"fallback": True}


def test_parse_json_from_llm_empty():
    result = parse_json_from_llm("")
    assert result == {}


def test_parse_json_from_llm_none():
    result = parse_json_from_llm(None, default={"none": True})
    assert result == {"none": True}


def test_parse_json_from_llm_nested():
    text = '{"plan": {"actions": [{"tool": "write", "params": {"path": "x.py"}}]}}'
    result = parse_json_from_llm(text)
    assert result["plan"]["actions"][0]["tool"] == "write"
