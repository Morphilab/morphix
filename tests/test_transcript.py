# tests/test_transcript.py
"""Tests del transcript event-sourced ligero (persistencia de contexto fina).

Hermeticidad: otros suites dejan tareas huérfanas en el executor compartido
que pueden escribir sus PROPIOS runs tardíos en el directorio de transcripts
activo mientras corre este test. Por eso las aserciones filtran SIEMPRE por
el archivo/huella del propio run y nunca asumen directorios vacíos ni sets
globales sin contaminación.
"""

import json

from core.transcript import (
    TranscriptLog,
    _sha1,
    find_open_runs,
    read_run,
    transcripts_dir,
)


def uuid4s():
    import uuid

    return uuid.uuid4().hex[:8]


# ── Unidad: secuencia, prefijo, persistencia ────────────────────────────


def test_run_completo_tiene_secuencia_y_cierre(tmp_path):
    log = TranscriptLog(tmp_path / "run.jsonl")
    log.append("run/start", agent_type="developer", task_sha1="abc")
    prev = None
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}]
    for i in range(1, 3):
        prev = log.step_request(i, msgs, prev)
        msgs.append({"role": "assistant", "content": f"a{i}"})
        log.step_response(i, i == 1, 20 * i)
    log.note("run/early_exit", iteration=2, reason="stalled")

    events = read_run(log.path)
    types = [e["type"] for e in events]
    assert types[0] == "run/start"
    assert types.count("step/request") == 2
    assert types[-1] == "run/early_exit"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1)), "seq contigua"


def test_prefix_broken_detectado_al_mutar_historial(tmp_path):
    log = TranscriptLog(tmp_path / "run.jsonl")
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u1"}]
    prev = log.step_request(1, msgs, None)

    # Simula compresión que muta el inicio (pérdida de system + u1)
    msgs_mutated = [{"role": "system", "content": "S-v2"}, {"role": "user", "content": "nuevo"}]
    log.step_request(2, msgs_mutated, prev)

    events = read_run(log.path)
    broken = [e for e in events if e["type"] == "cache/prefix_broken"]
    assert len(broken) == 1, f"la mutación del prefijo debe registrarse; events={events!r}"


def test_prefix_estable_no_alerta(tmp_path):
    log = TranscriptLog(tmp_path / "run.jsonl")
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u1"}]
    prev = log.step_request(1, msgs, None)
    # Crecimiento normal: mismo prefijo, se añade al final
    msgs.append({"role": "assistant", "content": "a1"})
    msgs.append({"role": "user", "content": "u2"})
    log.step_request(2, msgs, prev)
    events = read_run(log.path)
    assert not any(e["type"] == "cache/prefix_broken" for e in events), f"events={events!r}"


def test_cola_rasgada_ignorada_sin_explotar(tmp_path):
    p = tmp_path / "run.jsonl"
    good = {"seq": 1, "ts": "x", "type": "run/start"}
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(good) + "\n")
        f.write('{"seq": 2, "ts": "x", "ty')  # línea cortada por crash

    events = read_run(p)
    assert [e["seq"] for e in events] == [1], "cola rasgada descartada"


def test_fail_soft_en_escritura(tmp_path):
    """Un OSError en append jamás debe propagarse fuera del logger."""
    log = TranscriptLog(tmp_path / "no-dir" / "run.jsonl")
    seq = log.append("step/request", iteration=1)
    assert isinstance(seq, int)


def test_task_sha_estable_en_start():
    a = TranscriptLog.start(f"ws_a_{uuid4s()}", task="misma tarea")
    b = TranscriptLog.start(f"ws_b_{uuid4s()}", task="misma tarea")
    ev_a = read_run(a.path)[0]
    ev_b = read_run(b.path)[0]
    assert ev_a["task_sha1"] == ev_b["task_sha1"]


def test_find_open_runs_distingue_por_archivo(tmp_path):
    """Abierto/cerrado se decide por marcadores EN el archivo propio."""

    def _scan_open(d):
        opened = []
        for p in sorted(d.glob("*.jsonl")):
            evs = read_run(p)
            if (
                evs
                and evs[0].get("type") == "run/start"
                and not any(
                    e.get("type") in {"run/end", "run/cancelled", "run/early_exit"} for e in evs
                )
            ):
                opened.append(p.name)
        return opened

    abierto = TranscriptLog(tmp_path / "abierto.jsonl")
    abierto.append("run/start", task_sha1="x")

    cerrado = TranscriptLog(tmp_path / "cerrado.jsonl")
    cerrado.append("run/start", task_sha1="y")
    cerrado.append("run/end", status="completed", iterations=3)

    cancelado = TranscriptLog(tmp_path / "cancelado.jsonl")
    cancelado.append("run/start", task_sha1="z")
    cancelado.note("run/cancelled", iterations=1)

    open_names = _scan_open(tmp_path)
    assert abierto.path.name in open_names
    assert cerrado.path.name not in open_names
    assert cancelado.path.name not in open_names


# ── Integración con el agent loop ───────────────────────────────────────


async def test_loop_graba_transcript(tmp_path, monkeypatch):
    """execute_agent_loop escribe run/start + steps + run/end en SU JSONL."""
    from core.path_resolver import PathResolver

    base = tmp_path / "memory"
    monkeypatch.setattr(PathResolver, "memory_dir", staticmethod(lambda ws: base / ws))

    WS = f"loop_{uuid4s()}"
    TASK = f"haz algo simple {uuid4s()}"

    class _Msg:
        content = "Listo, tarea completada."
        tool_calls = None

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]

    async def fake_call(**kwargs):
        return _Resp()

    monkeypatch.setattr("llm.controller.ModelsController.call", staticmethod(fake_call))

    from orchestration.loop import AgentLoopConfig, execute_agent_loop

    result = await execute_agent_loop(
        task=TASK,
        workspace=WS,
        config=AgentLoopConfig(max_agent_iterations=2),
        allowed_tools=[],
    )
    assert result["status"] == "completed"

    expected_sha = _sha1(TASK)
    tdir = transcripts_dir(WS)

    # Localiza NUESTRO run por la huella de tarea del run/start
    ours = None
    for p in tdir.glob("*.jsonl"):
        evs = read_run(p)
        if evs and evs[0].get("type") == "run/start" and evs[0].get("task_sha1") == expected_sha:
            ours = evs
            break
    assert ours, "el loop debió grabar el transcript con nuestra huella"
    types = [e["type"] for e in ours]
    assert types[0] == "run/start"
    assert any(ty == "step/request" for ty in types)
    assert types[-1] == "run/end"
    assert ours[-1]["status"] == "completed"

    # Y nuestro archivo NO aparece entre los runs abiertos del workspace
    abiertos = find_open_runs(WS)
    our_name = next(
        p.name
        for p in tdir.glob("*.jsonl")
        if read_run(p) and read_run(p)[0].get("task_sha1") == expected_sha
    )
    assert our_name not in abiertos
