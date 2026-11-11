# tests/test_context_multimodal.py
"""estimate_tokens y build_context_summary deben tolerar content en bloques
(text/image_url): str(list) cuenta el repr — con base64 dispara la
estimación a megabytes y satura el presupuesto de tokens."""


from core.context_manager import ContextManager

BLOCKS = [
    {"type": "text", "text": "analiza"},
    {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + "A" * 400_000},
    },
]


def test_estimate_tokens_with_blocks_is_sane():
    msgs = [{"role": "user", "content": BLOCKS}]
    est = ContextManager.estimate_tokens(msgs)
    # texto(7) + marcador [image] + overhead ~4 tok; NUNCA escala con el b64
    assert est < 100, f"estimación inflada por repr del b64: {est}"


def test_content_to_text_variants():
    f = ContextManager._content_to_text
    assert f("hola") == "hola"
    assert f(None) in ("", "None")  # None → vacío o str, sin crash
    out = f(BLOCKS)
    assert "analiza" in out
    assert out.count("[image]") == 1
    assert "A" * 100 not in out, "el b64 no debe filtrarse al texto"


def test_build_context_summary_with_blocks():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": BLOCKS},
    ]
    summary = ContextManager.build_context_summary(msgs)
    assert "user" in summary and "[image]" in summary


def test_compress_history_with_blocks_keeps_message():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": BLOCKS},
        {"role": "assistant", "content": "respuesta"},
    ]
    out = ContextManager.compress_history(msgs, max_tokens=8000)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant"]
