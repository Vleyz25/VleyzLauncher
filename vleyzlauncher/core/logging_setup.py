"""Логи и единый перехват ошибок: чтобы падение не выглядело немым."""

from __future__ import annotations

import ctypes
import faulthandler
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from .. import APP_VERSION
from . import paths

LOG = logging.getLogger("vleyz")
_crash_file = None
_dialog_shown = False


def _show_error(exc: BaseException, where: str = "") -> None:
    global _dialog_shown
    if _dialog_shown:
        return
    _dialog_shown = True
    title = "Vleyz Launcher — ошибка"
    text = (
        f"{type(exc).__name__}: {exc}\n\n"
        f"Подробности записаны в лог:\n{paths.logs_dir() / 'launcher.log'}"
    )
    if where:
        text = f"{where}\n\n{text}"
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
            return
        except OSError:
            pass
    print(text, file=sys.stderr)


def _install_hooks(show_dialog: bool) -> None:
    previous = sys.excepthook

    def hook(exc_type, exc, traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, traceback)
            return
        LOG.critical("необработанная ошибка", exc_info=(exc_type, exc, traceback))
        if show_dialog:
            _show_error(exc)
        try:
            previous(exc_type, exc, traceback)
        except Exception:  # noqa: BLE001 - stderr может отсутствовать в windowed-сборке
            pass

    sys.excepthook = hook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        LOG.critical("ошибка в потоке %s", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        if show_dialog:
            _show_error(args.exc_value, f"Поток {name}")

    threading.excepthook = thread_hook


def setup_logging(show_dialog: bool = False) -> Path:
    """Включить файловый лог, faulthandler и перехват исключений."""
    global _crash_file
    folder = paths.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "launcher.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, logging.handlers.RotatingFileHandler) for item in root.handlers):
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
        if sys.stderr is not None:
            stream = logging.StreamHandler()
            stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
            root.addHandler(stream)

    # Падения на уровне нативного кода (Java/ctypes) тоже попадут в файл.
    if _crash_file is None:
        _crash_file = open(folder / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)

    _install_hooks(show_dialog)
    LOG.info(
        "Vleyz Launcher %s · Python %s · %s",
        APP_VERSION,
        sys.version.split()[0],
        "frozen" if getattr(sys, "frozen", False) else "source",
    )
    LOG.info("конфиг: %s", paths.config_path())
    return log_path
