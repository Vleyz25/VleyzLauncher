from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from . import secure
from .. import APP_VERSION, i18n

log = logging.getLogger(__name__)

APP_FOLDER = "VleyzLauncher"
INSTALL_MARKER = ".vleyz-install"
DATA_MARKER = ".vleyzlauncher"
PORTABLE_MARKER = ".vleyz-portable"
DEFAULT_NICKNAME = "Steve"

CONFIG_DEFAULTS = {
    "nickname": DEFAULT_NICKNAME,
    "ram_mb": 2048,
    "data_dir": "",
    "java_path": "",
    "selected_version": "",
    "selected_loader": "vanilla",
    "theme": "dark",
    "language": "ru",
    "jvm_args": "",
    "profiles": [],
    "active_profile": "",
    "ely_auth": {},
    "ely_enabled": False,
    "microsoft_auth": {},
    "account_mode": "offline",
    "servers": [],
}

# Что нельзя хранить открытым текстом. Пароль Ely.by нужен для
# перелогина, поэтому он тоже шифруется, а не выкидывается.
SECRET_FIELDS = {
    "ely_auth": ("login", "password", "client_token", "access_token"),
    "microsoft_auth": ("refresh_token", "access_token"),
}


def app_dir() -> Path:
    """Папка рядом с исполняемым файлом (или с исходниками при запуске из Python)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    """Личные данные лаунчера: конфиг, логи. Всегда в профиле пользователя."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_FOLDER
    return Path.home() / f".{APP_FOLDER.lower()}"


def is_portable() -> bool:
    """Лаунчер поставлен переносимо: игра и Java живут рядом с exe."""
    try:
        return (app_dir() / PORTABLE_MARKER).is_file()
    except OSError:
        return False


def default_data_dir() -> Path:
    if is_portable():
        return app_dir() / "minecraft"
    return state_dir() / "minecraft"


def java_root() -> Path:
    """Куда лаунчер ставит скачанные JRE."""
    if is_portable():
        return app_dir() / "java"
    return state_dir() / "java"


def logs_dir() -> Path:
    if is_portable():
        return app_dir() / "logs"
    return state_dir() / "logs"


def config_path() -> Path:
    return state_dir() / "config.json"


def legacy_config_path() -> Path:
    """Старое место конфига — рядом с exe. Нужно только для миграции."""
    return app_dir() / "config.json"


def _write_marker(folder: Path, name: str, kind: str) -> None:
    folder = Path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": kind,
            "app": APP_FOLDER,
            "version": APP_VERSION,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        (folder / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("маркер %s не записался в %s: %s", name, folder, exc)


def mark_install(folder: Path) -> None:
    """Помечает папку программы.

    Деинсталлятор удаляет только папки с этим маркером: если лаунчер стоит
    рядом с чужими файлами, они останутся на месте.
    """
    _write_marker(folder, INSTALL_MARKER, "install")


def mark_data(folder: Path) -> None:
    """Помечает папку игры, которой владеет лаунчер (versions, instances)."""
    _write_marker(folder, DATA_MARKER, "data")


def owned_by_launcher(folder: Path, marker: str = INSTALL_MARKER) -> bool:
    try:
        return (Path(folder) / marker).is_file()
    except OSError:
        return False


def ensure_data_root(folder: Path) -> bool:
    """Создаёт папку игры и ставит маркер. Возвращает True, если папка наша.

    Маркер ставится только на пустую папку или на уже помеченную: чужой
    каталог (старый .minecraft, папка с мирами) мы не объявляем своим, иначе
    деинсталлятор однажды его удалит.
    """
    folder = Path(folder)
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        mark_data(folder)
        return True
    if owned_by_launcher(folder, DATA_MARKER):
        return True
    try:
        empty = not any(folder.iterdir())
    except OSError:
        return False
    if empty:
        mark_data(folder)
        return True
    return False


def _map_secrets(cfg: dict, transform) -> dict:
    """Копия конфига, в которой секции с секретами обработаны transform."""
    result = dict(cfg)
    for section, fields in SECRET_FIELDS.items():
        block = cfg.get(section)
        if not isinstance(block, dict):
            continue
        updated = dict(block)
        for field in fields:
            value = updated.get(field)
            if isinstance(value, str) and value:
                updated[field] = transform(value)
        result[section] = updated
    return result


def _migrate_legacy() -> None:
    """Перенести config.json из папки программы в профиль пользователя."""
    target = config_path()
    if target.exists():
        return
    legacy = legacy_config_path()
    if not legacy.is_file():
        return
    try:
        stored = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("старый конфиг %s не прочитан: %s", legacy, exc)
        return
    if not isinstance(stored, dict):
        return
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(stored)
    if not save_config(cfg):
        return
    try:
        legacy.replace(legacy.with_name(legacy.name + ".migrated"))
    except OSError:
        # Файл мог быть только на чтение (Program Files) — не критично.
        pass
    log.info("конфиг перенесён из %s в %s", legacy, target)


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    _migrate_legacy()
    path = config_path()
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("конфиг %s не прочитан: %s", path, exc)
            stored = None
        if isinstance(stored, dict):
            # Незнакомые ключи сохраняем: их может добавить новая версия.
            cfg.update(stored)
    cfg = _map_secrets(cfg, secure.unprotect)
    if not cfg.get("data_dir"):
        cfg["data_dir"] = str(default_data_dir())
    return cfg


def save_config(cfg: dict) -> bool:
    """Атомарно сохранить конфиг, зашифровав секреты. True — записалось."""
    data = _map_secrets(cfg, secure.protect)
    path = config_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.error("не удалось сохранить настройки %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def data_dir(cfg: dict) -> Path:
    return Path(cfg.get("data_dir") or default_data_dir())


def human_size(num: float) -> str:
    num = float(max(0, num))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num < 1024 or unit == "ГБ":
            label = i18n.tr(unit)
            if unit == "Б":
                return f"{int(num)} {label}"
            value = f"{num:.1f}"
            if i18n.CURRENT == "ru":
                value = value.replace(".", ",")
            return f"{value} {label}"
        num /= 1024
    return f"{num:.1f} {i18n.tr('ГБ')}"


def human_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    return human_size(bps) + i18n.tr("/с")
