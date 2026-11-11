"""Tests for ChangeTracker — save, undo, redo, list, encode/decode."""

from core.change_tracker import ChangeTracker, _decode_path, _encode_path, get_tracker


class TestEncodeDecode:
    def test_roundtrip(self):
        original = "src/main.py"
        encoded = _encode_path(original)
        assert _decode_path(encoded) == original

    def test_special_chars(self):
        original = "src/my app/file_name.py"
        encoded = _encode_path(original)
        assert _decode_path(encoded) == original


class TestChangeTracker:
    def test_save_before_write_creates_backup(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        proj_dir = mem_dir
        test_file = proj_dir / "hello.py"
        test_file.write_text("original content")

        ct = ChangeTracker()
        ct._resolve = lambda fp: proj_dir / fp

        key = ct.save_before_write("hello.py")
        assert key is not None
        assert len(list(ct._undo_dir.glob("*"))) == 1

    def test_undo_restores_content(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        test_file = mem_dir / "hello.py"
        test_file.write_text("v1")

        ct = ChangeTracker()
        ct._resolve = lambda fp: mem_dir / fp

        ct.save_before_write("hello.py")
        test_file.write_text("v2")
        restored = ct.undo_last()

        assert restored == "hello.py"
        assert test_file.read_text() == "v1"

    def test_redo_reapplies_after_undo(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        test_file = mem_dir / "hello.py"
        test_file.write_text("v1")

        ct = ChangeTracker()
        ct._resolve = lambda fp: mem_dir / fp

        ct.save_before_write("hello.py")
        test_file.write_text("v2")
        ct.undo_last()
        assert test_file.read_text() == "v1"
        redo_path = ct.redo_last()
        assert redo_path == "hello.py"
        assert test_file.read_text() == "v2"

    def test_list_undo_stack(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        ct = ChangeTracker()
        ct._resolve = lambda fp: mem_dir / fp

        test_file = mem_dir / "a.py"
        test_file.write_text("a")
        ct.save_before_write("a.py")

        stack = ct.list_undo_stack()
        assert len(stack) == 1
        assert "a.py" in stack[0]

    def test_save_noop_when_file_missing(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        ct = ChangeTracker()
        ct._resolve = lambda fp: mem_dir / fp

        key = ct.save_before_write("nonexistent.py")
        assert key is None

    def test_undo_noop_when_empty_stack(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        ct = ChangeTracker()
        result = ct.undo_last()
        assert result is None


class TestGetTracker:
    def test_same_key_returns_same_instance(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        t1 = get_tracker("main", "proj")
        t2 = get_tracker("main", "proj")
        assert t1 is t2

    def test_different_key_returns_different_instance(self, tmp_path, monkeypatch):
        from core.path_resolver import paths

        mem_dir = tmp_path / "memory" / "main"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr(paths, "memory_dir", lambda ws: mem_dir)

        t1 = get_tracker("main", "proj_a")
        t2 = get_tracker("main", "proj_b")
        assert t1 is not t2
