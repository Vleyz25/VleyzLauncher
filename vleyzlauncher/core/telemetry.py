"""Однократный анонимный пинг установки.

Считает, сколько раз лаунчер поставили и запустили: один запрос на установку,
без ника, UUID, токенов и любых других данных. Отправляется один раз и только
если в конфиге не выключено (`telemetry: false`). Сетевые ошибки молча
игнорируются — на работу лаунчера это не влияет.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from . import net, paths

log = logging.getLogger(__name__)

COUNTER_URL = "https://abacus.jasoncameron.dev/hit/vleyzlauncher/installs"
MARKER = ".install-ping"
TIMEOUT = 6.0


def marker_path(directory: Path | None = None) -> Path:
    """Файл-отметка, что установка уже посчитана."""
    base = Path(directory) if directory else paths.state_dir()
    return base / MARKER


def is_enabled(cfg: dict) -> bool:
    """Статистика включена, если в конфиге не стоит telemetry: false."""
    return cfg.get("telemetry", True) is not False


def ping_install(
    cfg: dict,
    directory: Path | None = None,
    timeout: float = TIMEOUT,
) -> bool:
    """Отправляет один запрос на установку. True — если пинг ушёл сейчас."""
    if not is_enabled(cfg):
        return False
    marker = marker_path(directory)
    if marker.exists():
        return False
    try:
        net.fetch_text(COUNTER_URL, timeout=timeout, retries=1)
    except Exception as exc:  # noqa: BLE001 - статистика не должна ломать запуск
        log.info("пинг установки не отправлен: %s", exc)
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(COUNTER_URL, encoding="utf-8")
    except OSError as exc:
        log.info("отметка об установке не записана: %s", exc)
    return True


def ping_install_async(cfg: dict | None = None, directory: Path | None = None) -> threading.Thread:
    """Запускает пинг в фоне, чтобы не тормозить старт окна."""
    if cfg is None:
        try:
            cfg = paths.load_config()
        except Exception:  # noqa: BLE001 - конфиг может быть недоступен
            cfg = {}

    def run() -> None:
        ping_install(cfg, directory)

    thread = threading.Thread(target=run, name="install-ping", daemon=True)
    thread.start()
    return thread
