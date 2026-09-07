# tests/test_output_bounds.py — bounding de salida byte-exacto
"""Corte en frontera UTF-8, conteo EXACTO de bytes omitidos, y 'truncated'
significa SOLO 'el retainer recortó por presupuesto'."""

import pytest

from core.output_bounds import bound_output


def test_under_budget_untouched():
    r = bound_output("hola", max_bytes=100)
    assert r.text == "hola" and not r.truncated and r.omitted_bytes == 0


def test_cut_on_utf8_boundary_never_mojibake():
    text = "ñ" * 200  # ñ = 2 bytes UTF-8
    r = bound_output(text, max_bytes=200)  # margen para el marcador
    assert r.truncated
    assert len(r.text.encode("utf-8")) <= 200
    r.text.encode("utf-8")  # no lanza UnicodeDecodeError implícito
    assert "ñ" in r.text


def test_omitted_bytes_exact():
    text = "x" * 300
    r = bound_output(text, max_bytes=100)
    body_bytes = len(r.text.encode("utf-8")) - r.marker_bytes()
    assert r.omitted_bytes == 300 - body_bytes


def test_marker_present_and_inert():
    r = bound_output("y" * 500, max_bytes=64)
    assert "Omitidos" in r.text or "Omitted" in r.text
    assert str(r.omitted_bytes) in r.text


def test_tail_preserves_end():
    text = "HEAD" + "m" * 400 + "TAIL"
    r = bound_output(text, max_bytes=120, tail_bytes=8)
    assert "TAIL" in r.text
    assert r.text.startswith("HEAD")


def test_multibyte_split_not_counted_as_valid_char():
    # '€' son 3 bytes; con presupuesto 10 caben 3 '€' (9B) — el 4º corta
    text = "€" * 10
    r = bound_output(text, max_bytes=10)
    assert len(r.text.encode("utf-8")) <= 10


# ── Integración: cableado en tools ────────────────────────────────────────


@pytest.mark.asyncio
async def test_bash_output_bounded(tmp_path, monkeypatch):
    import core.path_resolver as pr
    import tools.bash_manager as bm

    monkeypatch.setattr(pr.paths, "memory_dir", lambda ws: tmp_path / "mem" / ws)
    monkeypatch.setattr(
        pr.paths,
        "code_projects_dir",
        lambda ws, root=None: tmp_path / "proj",
    )
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (("x" * 80_000).encode(), b"")

    async def fake_exec(*a, **k):
        return FakeProc()

    monkeypatch.setattr(bm.asyncio, "create_subprocess_shell", fake_exec)

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(bm.asyncio, "wait_for", fake_wait_for)

    proj = tmp_path / "mem" / "main"
    proj.mkdir(parents=True, exist_ok=True)
    res = await bm._bash_tool(command="cat big.txt", workspace="main", cwd=str(proj))
    assert res["success"] is True
    assert len(res["output"].encode()) <= 50_000 + 100
    assert "Omitidos" in res["output"] or "[+" in res["output"]


@pytest.mark.asyncio
async def test_file_read_bounded(tmp_path, monkeypatch):
    import core.path_resolver as pr
    from tools.file_manager import FileManager

    monkeypatch.setattr(pr.paths, "memory_base", lambda: tmp_path / "mem")

    f = tmp_path / "mem" / "main" / "grande.txt"
    f.parent.mkdir(parents=True)
    f.write_text("z" * 90_000, encoding="utf-8")

    out = await FileManager.execute("read", "grande.txt", workspace="main")
    assert len(out.encode("utf-8")) <= 50_000 + 100
    assert "Omitidos" in out or "[+" in out
