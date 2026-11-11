# tests/test_checkpoints.py — snapshots de proyecto con ledger y restore
"""Sin red y sin BD: filesystem tmp + monkeypatch de Paths (patrón del repo).

Contratos:
- identidad por proyecto (dir_hash): dos proyectos no comparten ledger;
- manifest con sha256 por fichero: drift detectado ANTES de escribir;
- restore honesto: sobrescribe capturados, deja los posteriores en su
  sitio (left_in_place), jamás borra implícitamente;
- guardas: rutas del manifiesto sin ../ ni absolutas; restore confinado
  a la raíz del proyecto;
- presupuestos: excluye .git/__pycache__/binarios; ficheros oversize se
  listan, no se copian en silencio;
- prune keep_last y delete puntual.
"""

import json

import pytest

from core import checkpoints as cp


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    monkeypatch.setattr(cp.Paths, "workspace_dir", staticmethod(lambda ws: ws_root / ws))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (proj / "src" / "b.py").write_text("B = 2\n", encoding="utf-8")
    (proj / ".git" / "objects").mkdir(parents=True)
    (proj / ".git" / "objects" / "x.bin").write_bytes(b"\x00" * 10)
    (proj / "__pycache__").mkdir()
    (proj / "__pycache__" / "a.pyc").write_bytes(b"\x00")
    return proj


def test_create_list_roundtrip(env):
    info = cp.create_checkpoint("ws", env, "antes del refactor")
    assert info["files"] == 2  # .git y __pycache__ excluidos
    assert info["skipped_oversize"] == []
    entries = cp.list_checkpoints("ws", env)
    assert len(entries) == 1 and entries[0]["checkpoint_id"] == info["checkpoint_id"]
    manifest = cp.get_checkpoint("ws", env, info["checkpoint_id"])
    assert {f["path"] for f in manifest["files"]} == {"src/a.py", "src/b.py"}
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_restore_overwrites_and_reports_left_in_place(env):
    before = cp.create_checkpoint("ws", env, "estado bueno")
    (env / "src" / "a.py").write_text("A = 'ROTO'\n", encoding="utf-8")
    (env / "src" / "nuevo.py").write_text("N = 3\n", encoding="utf-8")

    res = cp.restore_checkpoint("ws", env, before["checkpoint_id"])
    assert "src/a.py" in res["restored"]
    assert (env / "src" / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    # lo creado DESPUÉS no se borra: se reporta
    assert res["left_in_place"] == ["src/nuevo.py"]
    assert (env / "src" / "nuevo.py").exists()


def test_integrity_drift_blocks_restore(env):
    info = cp.create_checkpoint("ws", env, "integridad")
    snap = cp._store_root("ws", env) / info["checkpoint_id"] / "files" / "src" / "a.py"
    snap.write_text("TAMPERED\n", encoding="utf-8")
    with pytest.raises(cp.CheckpointError, match="integridad"):
        cp.restore_checkpoint("ws", env, info["checkpoint_id"])
    # el proyecto NO se tocó (verificación previa a toda escritura)
    assert (env / "src" / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_manifest_traversal_rejected(env):
    info = cp.create_checkpoint("ws", env, "sano")
    manifest = cp.get_checkpoint("ws", env, info["checkpoint_id"])
    manifest["files"].append({"path": "../fuera.txt", "bytes": 1, "sha256": "x" * 64})
    mp = cp._store_root("ws", env) / info["checkpoint_id"] / cp.MANIFEST_FILE
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cp.CheckpointError, match="inválida"):
        cp.restore_checkpoint("ws", env, info["checkpoint_id"])


def test_projects_isolated_by_dir_hash(env, tmp_path):
    otro = tmp_path / "otro-proj"
    otro.mkdir()
    (otro / "x.py").write_text("X\n", encoding="utf-8")
    cp.create_checkpoint("ws", env, "del primero")
    cp.create_checkpoint("ws", otro, "del segundo")
    assert cp.list_checkpoints("ws", env)[0]["description"] == "del primero"
    assert cp.list_checkpoints("ws", otro)[0]["description"] == "del segundo"
    # borrar el del segundo no toca al primero
    segundo_id = cp.list_checkpoints("ws", otro)[0]["checkpoint_id"]
    assert cp.delete_checkpoint("ws", otro, segundo_id) is True
    assert cp.delete_checkpoint("ws", otro, segundo_id) is False  # idempotente
    assert len(cp.list_checkpoints("ws", env)) == 1


def test_subset_paths_capture(env):
    info = cp.create_checkpoint("ws", env, "solo a", paths=["src/a.py"])
    manifest = cp.get_checkpoint("ws", env, info["checkpoint_id"])
    assert [f["path"] for f in manifest["files"]] == ["src/a.py"]
    with pytest.raises(cp.CheckpointError, match="no existe"):
        cp.create_checkpoint("ws", env, "fantasma", paths=["src/zz.py"])


def test_oversize_files_listed_not_copied(env, monkeypatch):
    monkeypatch.setattr(cp, "MAX_FILE_BYTES", 16)
    (env / "src" / "grande.py").write_text("X" * 100, encoding="utf-8")
    info = cp.create_checkpoint("ws", env, "con tope")
    assert info["skipped_oversize"] == ["src/grande.py"]
    assert info["files"] == 2


def test_prune_keeps_last_n(env):
    for i in range(4):
        (env / "src" / "a.py").write_text(f"A = {i}\n", encoding="utf-8")
        cp.create_checkpoint("ws", env, f"paso {i}")
    removed = cp.prune_checkpoints("ws", env, keep_last=2)
    assert removed == 2
    restantes = cp.list_checkpoints("ws", env)
    assert len(restantes) == 2
    assert restantes[0]["description"] == "paso 3"  # más reciente primero
    with pytest.raises(cp.CheckpointError):
        cp.prune_checkpoints("ws", env, keep_last=0)


def test_empty_description_rejected(env):
    with pytest.raises(cp.CheckpointError, match="description"):
        cp.create_checkpoint("ws", env, "   ")


def test_restore_missing_checkpoint_errors(env):
    with pytest.raises(cp.CheckpointError, match="no encontrado"):
        cp.restore_checkpoint("ws", env, "inexistente")
