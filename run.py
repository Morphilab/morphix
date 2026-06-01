#!/usr/bin/env python3
"""Morphix — PySide6 Desktop GUI entry point."""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── Early .env loading (before logging) ──
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
_LOG_LEVEL = logging.DEBUG if os.getenv("VERBOSE_LOGGING", "").lower() == "true" else logging.INFO

# ── Logging ──
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=_LOG_LEVEL,
    filename=os.path.join(LOG_DIR, "morphix.log"),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True,
    encoding="utf-8",
)

logger = logging.getLogger(__name__)
logger.info("=== Morphix Desktop iniciado ===")

# ── Carga temprana de tools + hooks ──
from core.hook_loader import load_global_hooks
from tools.loader import load_global_tools

load_global_tools()
load_global_hooks()

# ── Config validation ──
from core.bootstrap import validate_config

try:
    _valid, _warnings = validate_config()
    for w in _warnings:
        logger.warning("Config warning: %s", w)
except ValueError as e:
    logger.critical("Config validation failed: %s", e)
    print(f"FATAL: {e}", file=sys.stderr)
    sys.exit(1)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop.main_window import LoginDialog, MainWindow

_shutdown_flag = False


def _signal_handler(signum: int, frame) -> None:
    global _shutdown_flag
    if not _shutdown_flag:
        _shutdown_flag = True
        signame = signal.Signals(signum).name
        logger.info("Received %s, initiating graceful shutdown", signame)
        QApplication.instance().quit()


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    app = QApplication(sys.argv)
    app.setApplicationName("Morphix")
    app.setOrganizationName("MorphiLab")

    # ── Integrate asyncio event loop with Qt ──
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _init_task = None

    def _pump_asyncio():
        loop.call_soon(loop.stop)
        loop.run_forever()

    pump_timer = QTimer()
    pump_timer.timeout.connect(_pump_asyncio)
    pump_timer.start(16)

    # Login
    login = LoginDialog()
    if login.exec() != LoginDialog.DialogCode.Accepted:
        return

    # Main window
    window = MainWindow()
    window.show()

    # Inicializar backend
    async def init():
        try:
            await window.init_backend()
        except Exception as e:
            logger.critical(f"Error en init: {e}", exc_info=True)
            QMessageBox.critical(window, "Error", f"Error al inicializar:\n{e}")

    _init_task = asyncio.run_coroutine_threadsafe(init(), loop)

    # Save reference for cleanup in closeEvent
    window._init_task = _init_task

    exit_code = app.exec()

    # ── Graceful shutdown: stop daemons, cancel tasks, close loop ──
    from core.bootstrap import stop_daemons

    async def _shutdown():
        try:
            await stop_daemons()
        except Exception:
            logger.warning("Error stopping daemons", exc_info=True)

    loop.run_until_complete(_shutdown())

    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.close()
    logger.info("=== Morphix Desktop shut down gracefully ===")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
