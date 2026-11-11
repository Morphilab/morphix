# tests/test_fs_fence.py
"""Tests de la FS fence: contención de mutaciones bajo raíces escribibles."""

import pytest

from core.fs_fence import (
    canonicalize,
    check_write_target,
    is_within,
    writable_roots,
)
from core.path_resolver import paths
from tools.bash_manager import _fs_fence_check


def test_writable_roots_incluyen_workspace_y_tmp_aislado():
    roots = writable_roots("test_ws")
    ws_root = paths.memory_dir("test_ws").resolve()
    assert ws_root in roots
    # tmp aislado por workspace bajo memory/<ws>/tmp
    assert ws_root / "tmp" in roots
    # Sin duplicados
    assert len({str(r) for r in roots}) == len(roots)


def test_is_within_resuelve_symlinks():
    base = paths.memory_dir("test_ws").resolve()
    assert is_within(base / "sub/../file.txt", base) is True
    assert is_within(base / "ok.txt", base) is True


def test_check_write_target_permite_workspace():
    ws = paths.memory_dir("test_ws")
    allowed, reason = check_write_target(str(ws / "src" / "main.py"), "test_ws")
    assert allowed is True
    assert reason == ""


def test_check_write_target_permite_tmp_aislado():
    ws_root = paths.memory_dir("test_ws").resolve()
    ws_tmp = ws_root / "tmp"
    allowed, _ = check_write_target(str(ws_tmp / "morphix_test.txt"), "test_ws")
    assert allowed is True


def test_check_write_target_deniega_fuera_de_raices():
    for target in ("/etc/cron.d/evil", "/home/somebody/x", "/var/tmp/x", "/usr/bin/x"):
        allowed, reason = check_write_target(target, "test_ws")
        assert allowed is False, f"debió denegar: {target}"
        assert "[sandbox:" in reason


def test_check_write_target_deniega_symlink_escapando():
    """Un symlink dentro del workspace apuntando fuera debe denegarse."""
    ws_dir = paths.memory_dir("test_ws")
    ws_dir.mkdir(parents=True, exist_ok=True)
    link = ws_dir / "escape_link"
    try:
        link.symlink_to("/etc/passwd")
        allowed, _ = check_write_target(str(link), "test_ws")
        assert allowed is False, "symlink que escapa del workspace debió denegarse"
    except OSError:
        pytest.skip("sin permiso para crear symlinks")
    finally:
        link.unlink(missing_ok=True)


def test_fs_fence_bloquea_escritura_absoluta_fuera():
    ok, reason = _fs_fence_check("echo x > /etc/cron.d/evil", "test_ws")
    assert ok is False
    assert "[sandbox:" in reason


def test_fs_fence_bloquea_verbos_de_archivo_fuera():
    ok, _ = _fs_fence_check("touch /home/user/pwned.txt", "test_ws")
    assert ok is False
    ok, _ = _fs_fence_check("cp /etc/passwd /home/user/copy", "test_ws")
    assert ok is False
    ok, _ = _fs_fence_check("mkdir -p /root/proyecto", "test_ws")
    assert ok is False


def test_fs_fence_permite_relativo_y_tmp_aislado():
    ws_root = paths.memory_dir("test_ws").resolve()
    ws_tmp = ws_root / "tmp"
    ok, reason = _fs_fence_check("echo hi > log.txt", "test_ws")
    assert ok is True, reason
    ok, _ = _fs_fence_check(f"rm {ws_tmp}/archivo_tmp.txt", "test_ws")
    assert ok is True
    ok, _ = _fs_fence_check("touch nuevo.py && echo ok > out.log", "test_ws")
    assert ok is True


def test_fs_fence_permite_dev_null():
    ok, reason = _fs_fence_check("echo x > /dev/null", "test_ws")
    assert ok is True, reason


def test_fs_fence_bloquea_tee_pipe_fuera():
    """tee en pipe hacia path fuera del workspace debe bloquearse."""
    ok, reason = _fs_fence_check("echo secret | tee /etc/passwd", "test_ws")
    assert ok is False
    assert "[sandbox:" in reason


def test_fs_fence_permite_tee_pipe_dentro():
    """tee en pipe hacia path dentro del workspace debe permitirse."""
    ws_root = paths.memory_dir("test_ws").resolve()
    ok, reason = _fs_fence_check(f"echo hi | tee {ws_root}/out.txt", "test_ws")
    assert ok is True, reason


def test_fs_fence_permite_comandos_sin_escritura():
    ok, reason = _fs_fence_check("ls -la && python3 script.py", "test_ws")
    assert ok is True, reason


def test_canonicalize_resuelve_relativos():
    ws = paths.memory_dir("test_ws")
    assert canonicalize(str(ws / "a/../b.txt")) == canonicalize(str(ws / "b.txt"))


# ── bypass por flags del verbo ─────────────────────────────────────


def test_fs_fence_tee_con_flag_a():
    """`tee -a /etc/passwd`: antes se capturaba '-a' (relativa→permitida)."""
    ok, reason = _fs_fence_check("echo x | tee -a /etc/passwd", "test_ws")
    assert ok is False, f"tee -a con destino fuera debió bloquearse: {reason}"
    assert "[sandbox:" in reason


def test_fs_fence_cp_con_flag_f():
    """`cp -f notas.txt /etc/cron.d/evil`: antes se capturaba el SOURCE."""
    ok, _ = _fs_fence_check("cp -f notas.txt /etc/cron.d/evil", "test_ws")
    assert ok is False


def test_fs_fence_mv_install_flags():
    ok, _ = _fs_fence_check("mv -f x.txt /root/pwned.txt", "test_ws")
    assert ok is False
    ok, _ = _fs_fence_check("install -m 644 a.txt /etc/cron.d/x", "test_ws")
    assert ok is False


def test_fs_fence_nuevos_verbos():
    """dd of=, sed -i, truncate, ln -sf no estaban cubiertos."""
    ok, _ = _fs_fence_check("dd if=/dev/zero of=/root/zero bs=1 count=1", "test_ws")
    assert ok is False
    ok, _ = _fs_fence_check("sed -i 's/a/b/' /home/user/config.ini", "test_ws")
    assert ok is False
    ok, _ = _fs_fence_check("truncate -s 0 /var/log/wtmp", "test_ws")
    assert ok is False
    ok, _ = _fs_fence_check("ln -sf /etc/passwd /home/user/leak", "test_ws")
    assert ok is False


def test_fs_fence_flags_con_destino_interno_sigue_permitido():
    """Los flags legítimos con destino DENTRO del workspace no rompen."""
    ok, reason = _fs_fence_check("echo x | tee -a log.txt", "test_ws")
    assert ok is True, reason
    ok, _ = _fs_fence_check("cp -f notas.txt notas_v2.txt", "test_ws")
    assert ok is True
    ok, _ = _fs_fence_check("sed -i 's/a/b/' config.ini", "test_ws")
    assert ok is True


# ── bypass por expansión de tilde ──────────────────────────────────


def test_fs_fence_tilde_user_fuera():
    """`~root/...` el shell lo expande a /root/... — fuera de raíces."""
    ok, _ = _fs_fence_check("echo x > ~root/.bashrc", "test_ws")
    assert ok is False


def test_fs_fence_tilde_bare_resuelve_al_home_del_hijo():
    """`~/x` el hijo tiene HOME=memory/<ws> → dentro de raíces → permitido."""
    ok, reason = _fs_fence_check("echo x > ~/out.txt", "test_ws")
    assert ok is True, reason


def test_fs_fence_tilde_escape_por_puntos():
    """`~/../x` con HOME=memory/<ws> trepa fuera de la raíz."""
    ok, _ = _fs_fence_check("echo x > ~/../../tmp/pwned", "test_ws")
    assert ok is False
