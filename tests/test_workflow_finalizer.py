# tests/test_workflow_finalizer.py
"""Tests para el finalizador de workflows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.finalizer import (
    _extract_personal_facts,
    _truncate_safe_summary,
    finalize_workflow,
)


@pytest.fixture
def mock_deps(tmp_path):
    """Fixture base: mocks para BD, memoria, git, métricas.

    ``memory_dir`` apunta a tmp: el journal del finalizer es best-effort y
    sin este aislamiento la suite escribe ruido en el journal REAL del
    workspace.
    """
    from core import path_resolver

    with (
        patch(
            "orchestration.finalizer.ConversationRepository.save",
            new_callable=AsyncMock,
        ) as mock_save,
        patch(
            "orchestration.finalizer.get_async_session",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.finalizer.memory_manager.update_user_profile",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.finalizer.memory_manager.write",
            new_callable=AsyncMock,
        ),
        patch.object(path_resolver.paths, "memory_dir", lambda ws: tmp_path),
    ):
        mock_save.return_value = 42
        yield mock_save


@pytest.mark.asyncio
async def test_finalize_workflow_basic(mock_deps):
    """Verifica que finalize_workflow completa sin errores con datos mínimos."""
    events = MagicMock()

    await finalize_workflow(
        query="Crear test.py",
        final_output="Archivo creado exitosamente.",
        conversation_history=[{"role": "user", "content": "Crear test.py"}],
        scorecard={"subtasks": 1, "tokens": 100},
        subtasks_list=["Crear test.py"],
        task_analysis={"primary_type": "ejecutor"},
        events=events,
    )
    mock_deps.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_workflow_with_files_does_NOT_commit(mock_deps):
    """El auto-commit implícito está eliminado — files_written ya
    NO dispara smart_auto_commit (el commit es decisión del agente o del
    workflow via commit_after)."""
    with patch(
        "core.git_operations.smart_auto_commit",
        new_callable=AsyncMock,
    ) as mock_commit:
        events = MagicMock()

        await finalize_workflow(
            query="commit test",
            final_output="done",
            conversation_history=[{"role": "user", "content": "commit test"}],
            scorecard={"subtasks": 1, "tokens": 50},
            subtasks_list=["task"],
            task_analysis={"primary_type": "ejecutor"},
            events=events,
            project_root="code_projects/app",
            files_written=["app.py"],
        )
        mock_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_workflow_handles_db_error(mock_deps):
    """Verifica que un error de BD no crashea el finalizador."""
    mock_deps.side_effect = RuntimeError("BD caída")

    events = MagicMock()

    # No debe lanzar excepción
    await finalize_workflow(
        query="test",
        final_output="ok",
        conversation_history=[],
        scorecard={"subtasks": 0, "tokens": 0},
        subtasks_list=[],
        task_analysis={},
        events=events,
    )


class TestExtractPersonalFacts:
    @pytest.mark.asyncio
    async def test_extract_facts_returns_dict(self):
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name":"Ana","city":"Lima"}'

            result = await _extract_personal_facts("Hola", "Me llamo Ana y vivo en Lima")
            assert isinstance(result, dict)
            assert result.get("name") == "Ana"

    @pytest.mark.asyncio
    async def test_extract_facts_handles_llm_error(self):
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.side_effect = RuntimeError("LLM error")
            result = await _extract_personal_facts("Hola", "test")
            assert result == {}

    @pytest.mark.asyncio
    async def test_extract_facts_handles_invalid_json(self):
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = "not json"

            result = await _extract_personal_facts("Hola", "test")
            assert result == {}

    @pytest.mark.asyncio
    async def test_extract_facts_name_only_survives_extraction(self):
        """El nombre solo NO se descarta en la extracción: el gate contextual
        (perfil vacío vs poblado) vive en update_user_profile — la extracción
        entrega el hecho tal cual lo dijo el usuario."""
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name":"Ana"}'

            result = await _extract_personal_facts("Hola", "Me llamo Ana")
            assert result == {"name": "Ana"}

    @pytest.mark.asyncio
    async def test_extract_facts_name_only_returns_facts(self):
        """La extracción entrega el hecho tal cual: el gate anti-stale es
        CONTEXTUAL (perfil vacío se siembra; poblado no se toca) y vive en
        update_user_profile, no en la extracción."""
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name": "Asistente"}'

            result = await _extract_personal_facts("Hola", "Crea script.py")
            assert result == {"name": "Asistente"}

    @pytest.mark.asyncio
    async def test_extract_facts_keeps_name_with_context(self):
        """Un nombre acompañado de otro dato contextual SÍ es un hecho válido."""
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name": "Ana", "city": "Lima"}'

            result = await _extract_personal_facts("Hola", "Me llamo Ana y vivo en Lima")
            assert result.get("name") == "Ana"
            assert result.get("city") == "Lima"

    def test_prompt_template_sin_datos_personales(self):
        """El esqueleto del prompt de extracción no contiene datos de muestra:
        un eco del propio prompt jamás puede sembrar un 'dato' personal."""
        from orchestration.finalizer import _PROFILE_PROMPT_TEMPLATE

        for muestra in ("SampleName", "SampleCity", "SamplePet", "SampleFood", "SampleColor"):
            assert muestra not in _PROFILE_PROMPT_TEMPLATE

    @pytest.mark.asyncio
    async def test_guard_descarta_valores_eco_del_prompt(self):
        """Un valor que ya vive en el esqueleto del prompt es un eco del
        extractor — se descarta; los hechos legítimos del texto pasan."""
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = (
                '{"name": "name", "city": "Guadalajara", "age": "Responde"}'
            )

            result = await _extract_personal_facts("Hola", "Me llamo Ana y vivo en Guadalajara")
            assert "name" not in result
            assert "age" not in result
            assert result.get("city") == "Guadalajara"

    @pytest.mark.asyncio
    async def test_finalize_no_siembra_hechos_eco(self, tmp_path):
        """Si la extracción solo produce ecos del prompt, el perfil no se toca."""
        from core import path_resolver

        with (
            patch(
                "llm.controller.models.call",
                new_callable=AsyncMock,
            ) as mock_call,
            patch(
                "orchestration.finalizer.memory_manager.update_user_profile",
                new_callable=AsyncMock,
            ) as mock_profile,
            patch.object(path_resolver.paths, "memory_dir", lambda ws: tmp_path),
            patch(
                "orchestration.finalizer.get_async_session",
                new_callable=AsyncMock,
            ),
            patch(
                "orchestration.finalizer.ConversationRepository.save",
                new_callable=AsyncMock,
            ),
            patch(
                "orchestration.finalizer.memory_manager.write",
                new_callable=AsyncMock,
            ),
        ):
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            # "Texto" es parte del esqueleto del prompt — el eco completo
            # deja facts vacío y el perfil no se toca.
            mock_call.return_value.choices[0].message.content = '{"name": "Texto"}'

            events = MagicMock()
            await finalize_workflow(
                query="tarea sin datos personales",
                final_output="Trabajo terminado.",
                conversation_history=[],
                scorecard={"subtasks": 1, "tokens": 10},
                subtasks_list=["tarea"],
                task_analysis={"primary_type": "ejecutor"},
                events=events,
            )
            mock_profile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_finalize_log_no_miente_si_el_gate_rechaza(self, tmp_path, caplog):
        """update_user_profile puede rechazar; el log no debe decir 'actualizado'."""
        from core import path_resolver

        caplog.set_level("INFO")
        with (
            patch(
                "orchestration.finalizer._extract_personal_facts",
                new_callable=AsyncMock,
                return_value={"name": "Ana"},
            ),
            patch(
                "orchestration.finalizer.memory_manager.update_user_profile",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(path_resolver.paths, "memory_dir", lambda ws: tmp_path),
            patch(
                "orchestration.finalizer.get_async_session",
                new_callable=AsyncMock,
            ),
            patch(
                "orchestration.finalizer.ConversationRepository.save",
                new_callable=AsyncMock,
            ),
            patch(
                "orchestration.finalizer.memory_manager.write",
                new_callable=AsyncMock,
            ),
        ):
            await finalize_workflow(
                query="me llamo Ana",
                final_output="Hola Ana.",
                conversation_history=[],
                scorecard={"subtasks": 1, "tokens": 10},
                subtasks_list=["tarea"],
                task_analysis={"primary_type": "ejecutor"},
                events=MagicMock(),
            )
        assert "no actualizado" in caplog.text
        assert "actualizado con" not in caplog.text


class TestTruncateSafeSummary:
    def test_short_summary_unchanged(self):
        assert _truncate_safe_summary("hola mundo") == "hola mundo"

    def test_exactly_4000_chars_not_truncated(self):
        """Un resumen de exactamente 4000 chars NO fue cortado — no debe recortarse."""
        s = "a" * 4000
        assert _truncate_safe_summary(s) == s

    def test_over_4000_truncated_to_last_period(self):
        s = ("a" * 3000) + ". fin de frase " + ("b" * 2000)
        out = _truncate_safe_summary(s)
        assert len(out) <= 4000
        assert out.endswith(".")
        assert len(out) < len(s)


class TestLastTaskSummaryHonesto:
    @pytest.mark.asyncio
    async def test_boilerplate_de_stall_se_etiqueta(self, mock_deps):
        """El output crudo '⚠️ Límite de N iteraciones...' no describe el
        trabajo: el resumen guardado lleva etiqueta honesta y la consulta."""
        from orchestration.finalizer import memory_manager

        stall_output = (
            "⚠️ Límite de 8 iteraciones alcanzado.\n"
            "Acciones ejecutadas: 17. Archivos modificados: 7.\n"
            "La tarea podría necesitar descomponerse en partes más pequeñas."
        )
        await finalize_workflow(
            query="especifica un contador de palabras",
            final_output=stall_output,
            conversation_history=[],
            scorecard={"subtasks": 12, "tokens": 99000},
            subtasks_list=["x"],
            task_analysis={"primary_type": "dsl_workflow"},
            events=MagicMock(),
        )
        written = memory_manager.write.call_args_list[-1]
        assert written.args[0] == "last_task_summary"
        contenido = written.args[1]
        assert "pasos degradados" in contenido
        assert "especifica un contador de palabras" in contenido
        assert "Límite de 8 iteraciones" not in contenido

    @pytest.mark.asyncio
    async def test_boilerplate_de_stall_con_warning_tambien_se_etiqueta(self, mock_deps):
        """El warning prepend no puede desactivar el resumen honesto del stall."""
        from orchestration.finalizer import memory_manager
        from orchestration.workflows.orchestrator import _append_quality_warning

        stall_output = _append_quality_warning(
            "⚠️ Límite de 8 iteraciones alcanzado.\nAcciones ejecutadas: 17.",
            {"calidad": "Parcial", "degradadas": 3},
            ["tool:test_runner"],
        )
        await finalize_workflow(
            query="especifica un contador de palabras",
            final_output=stall_output,
            conversation_history=[],
            scorecard={"subtasks": 12, "tokens": 99000},
            subtasks_list=["x"],
            task_analysis={"primary_type": "dsl_workflow"},
            events=MagicMock(),
        )
        written = memory_manager.write.call_args_list[-1]
        contenido = written.args[1]
        assert "pasos degradados" in contenido
        assert "Límite de 8 iteraciones" not in contenido

    @pytest.mark.asyncio
    async def test_resumen_normal_se_guarda_intacto(self, mock_deps):
        from orchestration.finalizer import memory_manager

        await finalize_workflow(
            query="q",
            final_output="Resumen normal del trabajo.",
            conversation_history=[],
            scorecard={"subtasks": 1, "tokens": 10},
            subtasks_list=["x"],
            task_analysis={"primary_type": "ejecutor"},
            events=MagicMock(),
        )
        written = memory_manager.write.call_args_list[-1]
        assert written.args[1] == "Resumen normal del trabajo."


class TestJournalYCandidatas:
    def test_journal_anota_una_linea_por_run(self, tmp_path, monkeypatch):
        from core import path_resolver
        from orchestration.finalizer import _append_journal

        monkeypatch.setattr(path_resolver.paths, "memory_dir", lambda ws: tmp_path)
        _append_journal("main", "tarea de prueba", {"calidad": "Alta"}, 7)
        _append_journal("main", "otra tarea", {"calidad": "Parcial"}, 8)

        from datetime import datetime

        path = tmp_path / "journal" / f"{datetime.now():%Y-%m-%d}.md"
        texto = path.read_text(encoding="utf-8")
        assert "# Journal" in texto
        assert "tarea de prueba" in texto and "(Alta)" in texto
        assert "otra tarea" in texto and "(Parcial)" in texto
        assert texto.count("[conv") == 2

    def test_candidata_solo_con_alta_y_archivos(self, tmp_path, monkeypatch):
        import json

        from core import path_resolver
        from orchestration.finalizer import _record_skill_candidate

        monkeypatch.setattr(path_resolver.paths, "memory_dir", lambda ws: tmp_path)
        # Parcial → no registra
        _record_skill_candidate("main", "t1", {"calidad": "Parcial"}, ["a.py"])
        # Alta sin archivos → no registra
        _record_skill_candidate("main", "t2", {"calidad": "Alta"}, [])
        # Alta con archivos → registra
        _record_skill_candidate("main", "t3", {"calidad": "Alta"}, ["a.py", "b.py"])
        # duplicado exacto → no re-registra
        _record_skill_candidate("main", "t3", {"calidad": "Alta"}, ["a.py"])

        data = json.loads((tmp_path / "skills" / ".candidates.json").read_text())
        assert len(data) == 1
        assert data[0]["task"] == "t3"
        assert data[0]["files"] == ["a.py", "b.py"]
