# tests/test_desktop_sanitize.py
"""Saneamiento de rich-text (LLM output) y quoting de lnav."""


def test_sanitize_strips_script_tags():
    from desktop.sanitize import sanitize_rich_text

    text = "hola <script>alert('x')</script> mundo"
    out = sanitize_rich_text(text)
    assert "script" not in out.lower()


def test_sanitize_strips_javascript_links():
    from desktop.sanitize import sanitize_rich_text

    text = "[clic](javascript:alert(1)) y [otro](JaVaScRiPt:evil())"
    out = sanitize_rich_text(text)
    assert "javascript" not in out.lower()


def test_sanitize_keeps_safe_markdown():
    from desktop.sanitize import sanitize_rich_text

    text = "**negrita** y [link seguro](https://example.com) y `code`"
    out = sanitize_rich_text(text)
    assert "**negrita**" in out
    assert "https://example.com" in out


def test_sanitize_used_by_chat_bubble_and_history():
    """Los sitios que renderizan salida del LLM deben pasar por el sanitizer."""
    import inspect

    import desktop.history_tab
    import desktop.widgets.chat_bubble as cb

    cb_src = inspect.getsource(cb)
    hist_src = inspect.getsource(desktop.history_tab)
    assert "sanitize_rich_text" in cb_src
    assert "sanitize_rich_text" in hist_src


def test_lnav_command_quotes_log_path(monkeypatch, tmp_path):
    """El path del log se cita para que el shell del emulador no lo reinterprete."""
    from unittest.mock import MagicMock, patch

    import desktop.services.dashboard_service as svc

    log_file = tmp_path / "morphix dir" / "morphix.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("log")
    monkeypatch.setattr(svc.paths, "log_file", lambda: log_file)
    monkeypatch.setattr(svc.platform, "system", lambda: "Linux")

    popen_mock = MagicMock()
    with patch("subprocess.Popen", popen_mock):
        result = svc.DashboardService.open_logs_lnav()
        assert result["success"] is True
        cmd_args = popen_mock.call_args.args[0]
        joined = " ".join(str(a) for a in cmd_args)
        assert "'" in joined  # shlex.quote aplica comillas al path con espacios
        assert "morphix dir" in joined
