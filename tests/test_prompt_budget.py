"""Presupuesto token-aware del system prompt por capas."""

from orchestration.prompt_budget import clip_to_budget, count_tokens, enforce_budget


def test_clip_respects_token_budget():
    text = "\n".join(f"linea {i} con algo de contenido" for i in range(500))
    clipped = clip_to_budget(text, max_tokens=50)
    assert count_tokens(clipped) <= 60  # margen del marker
    assert clipped.endswith("[…truncado por presupuesto]")


def test_clip_noop_when_under_budget():
    text = "corto"
    assert clip_to_budget(text, max_tokens=100) == text


def test_enforce_budget_drops_lowest_priority_layer():
    big = "palabra " * 5000
    result = enforce_budget(
        [("reglas", "REGLAS FIJAS", 600), ("perfil", big, 400)],
        hard_cap=700,
    )
    assert "REGLAS FIJAS" in result  # prioridad alta sobrevive
    assert count_tokens(result) <= 750


def test_enforce_budget_keeps_all_when_room():
    result = enforce_budget(
        [("a", "capa A", 50), ("b", "capa B", 50)],
        hard_cap=500,
    )
    assert "capa A" in result and "capa B" in result


def test_enforce_budget_skips_empty_layers(caplog):
    """Capa sin contenido (bot sin memoria/skills aún) NO es un recorte por
    presupuesto: no debe emitir el warning engañoso 'presupuesto agotado'."""
    with caplog.at_level("WARNING"):
        result = enforce_budget([("vacía", "   ", 100), ("buena", "contenido real", 50)], 500)
    assert "contenido real" in result
    assert not caplog.messages, f"capa vacía no debe advertir: {caplog.messages}"


def test_enforce_budget_warns_only_on_real_cut(caplog):
    """El warning 'presupuesto agotado' queda SOLO para capas descartadas por
    recorte real (su allowance llega a 0), no para capas vacías."""
    with caplog.at_level("WARNING"):
        result = enforce_budget(
            [("prio", "contenido que llena la capa prioritaria", 100), ("cola", "no cabe", 0)],
            hard_cap=500,
        )
    assert "no cabe" not in result
    assert any("presupuesto agotado" in m for m in caplog.messages), caplog.messages


def test_skills_kits_have_dedicated_layer():
    """Los kits/skills viven en capa propia, NO dentro de contexto_proyecto."""
    from orchestration.loop import _system_prompt_layers

    layers = _system_prompt_layers(
        react_rules="REGLAS",
        enriched_context="CONTEXTO-FAISS",
        profile_context="PERFIL",
        skills_kits_context="[TOOL KITS — ...]",
    )
    by_name = {name: text for name, text, _cap in layers}
    assert "skills_kits" in by_name
    assert "[TOOL KITS" in by_name["skills_kits"]
    assert "[TOOL KITS" not in by_name["contexto_proyecto"]
    assert by_name["contexto_proyecto"] == "CONTEXTO-FAISS"


def test_kits_no_longer_displace_project_context():
    """Con kits presentes, el contexto de proyecto entra completo
    (capas independientes, cada una con su propio cap)."""
    big_ctx = "\n".join(f"contexto-línea-{i}" for i in range(100))
    big_kits = "\n".join(f"kits-línea-{i}" for i in range(100))
    from orchestration.loop import _system_prompt_layers

    out = enforce_budget(_system_prompt_layers("R", big_ctx, "", big_kits), hard_cap=20_000)
    assert "contexto-línea-99" in out, "el contexto de proyecto fue desplazado"
    assert "kits-línea-99" in out, "los kits fueron desplazados"


def test_system_prompt_bounded_in_loop():
    """Integración: el system_msg del loop respeta el hard_cap con contextos gigantes."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = {"name": "Ana"}

    giant_ctx = "contexto " * 200_000  # ~400k chars

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "ok"
    final_response.choices[0].message.tool_calls = None
    final_response.usage = None

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock),
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_idx,
        patch("orchestration.loop._build_extra_context", new=AsyncMock(return_value=giant_ctx)),
    ):
        mock_idx.return_value.index_project.return_value = None
        mock_idx.return_value.find_relevant_code.return_value = ""

        from orchestration.loop import execute_agent_loop

        asyncio.run(
            execute_agent_loop(task="tarea", agent_type="developer", history=[], workspace="main")
        )

        system_content = mock_llm.call_args.kwargs["messages"][0]["content"]

    from core.config import settings

    expected_cap = max(2000, settings.max_context_tokens // 4)
    assert (
        count_tokens(system_content) <= expected_cap + 50
    ), f"system prompt sin techo ({count_tokens(system_content)} tokens)"


def test_fit_marked_block_conserva_cierre_con_contenido_token_denso():
    """Los caps de capa son TOKENS y el clip es por líneas: un bloque token-denso
    (CJK) perdería su línea de cierre y la capa siguiente quedaría pegada."""
    from orchestration.prompt_budget import fit_marked_block

    body = "Goal: " + "测试" * 900 + "\nRules: no repitas"
    out = fit_marked_block(body, "<RUN_STATE>", "</RUN_STATE>", max_chars=2000, max_tokens=760)

    assert out.startswith("<RUN_STATE>")
    assert out.endswith("</RUN_STATE>")
    assert count_tokens(out) <= 760
    assert len(out) <= 2000


def test_fit_marked_block_respeta_caps_chicos_y_conserva_el_sufijo():
    from orchestration.prompt_budget import fit_marked_block

    body = "\n".join(f"linea {i} con suficiente texto para cortar" for i in range(200))
    out = fit_marked_block(body, "<BLACKBOARD>", "</BLACKBOARD>", max_chars=400, max_tokens=120)

    assert out.endswith("</BLACKBOARD>")
    assert len(out) <= 400
    assert count_tokens(out) <= 120


def test_fit_marked_block_devuelve_vacio_si_los_marcadores_no_caben():
    from orchestration.prompt_budget import fit_marked_block

    assert (
        fit_marked_block("x" * 100, "<RUN_STATE>", "</RUN_STATE>", max_chars=20, max_tokens=10)
        == ""
    )


def test_fit_marked_block_no_corta_sin_marcador_de_truncado():
    from orchestration.prompt_budget import fit_marked_block

    body = "palabra " * 500
    out = fit_marked_block(body, "<X>", "</X>", max_chars=200, max_tokens=80)
    assert "…" in out


def test_fit_marked_block_token_largo_conserva_el_sufijo():
    """Un solo token largo (sin espacios) también señala el truncado."""
    from orchestration.prompt_budget import fit_marked_block

    out = fit_marked_block("x" * 3000, "<X>", "</X>", max_chars=200, max_tokens=30)

    assert out.endswith("</X>")
    assert "…" in out
