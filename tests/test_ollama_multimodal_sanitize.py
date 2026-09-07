# tests/test_ollama_multimodal_sanitize.py — sanitizador multimodal de Ollama
"""sanitize_messages_for_ollama debe convertir bloques OpenAI-style
(text/image_url) al formato nativo de Ollama: content:str + images:[b64].
Sin el sanitizador los bloques pasan tal cual ⇒ ValidationError del SDK."""

from llm.tool_calls import sanitize_messages_for_ollama


def _user_blocks(*blocks):
    return [{"role": "user", "content": list(blocks)}]


def test_content_blocks_become_text_plus_images():
    msgs = _user_blocks(
        {"type": "text", "text": "describe esto"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
        },
    )
    out = sanitize_messages_for_ollama(msgs)[0]
    assert out["content"] == "describe esto"
    assert out["images"] == ["iVBORw0KGgo="]
    assert not isinstance(out["content"], list)


def test_multiple_texts_and_images_ordered():
    msgs = _user_blocks(
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        {"type": "image_url", "image_url": {"url": "data:image/gif;base64,BBB"}},
    )
    out = sanitize_messages_for_ollama(msgs)[0]
    assert out["content"] == "a\nb"
    assert out["images"] == ["AAA", "BBB"]


def test_image_url_without_data_prefix_treated_as_raw_b64():
    msgs = _user_blocks(
        {"type": "text", "text": "x"},
        {"type": "image_url", "image_url": {"url": "RAWB64=="}},
    )
    out = sanitize_messages_for_ollama(msgs)[0]
    assert out["images"] == ["RAWB64=="]


def test_string_content_and_tool_call_conversion_untouched():
    """No-regresión: mensajes normales y conversión string→dict de args."""
    msgs = [
        {"role": "system", "content": "eres útil"},
        {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": '{"a": 1}'}}],
        },
    ]
    out = sanitize_messages_for_ollama(msgs)
    assert out[0]["content"] == "eres útil"
    assert "images" not in out[0]
    assert out[1]["tool_calls"][0]["function"]["arguments"] == {"a": 1}


def test_original_list_not_mutated():
    msgs = _user_blocks(
        {"type": "text", "text": "t"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZQ=="}},
    )
    sanitize_messages_for_ollama(msgs)
    assert isinstance(msgs[0]["content"], list), "input mutado"
