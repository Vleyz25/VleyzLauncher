from __future__ import annotations

import json
import logging
import os
import shutil
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
CONFIG_VERSION = 2

CONFIG_DEFAULTS = {
    "config_version": CONFIG_VERSION,
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


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
            os.path.normpath(str(right))
        )
    except (TypeError, ValueError):
        return False


def migrate_portable_data() -> Path | None:
    """Переносит старую папку игры в папку лаунчера при переносимой установке.

    Нужно, когда человек ставит 1.9+ поверх 1.8: данные лежали в
    ``%APPDATA%\\VleyzLauncher\\minecraft``, а теперь должны жить рядом с exe.
    """
    if not is_portable():
        return None
    source = state_dir() / "minecraft"
    target = app_dir() / "minecraft"
    if not source.is_dir() or target.exists():
        return None
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        log.warning("не удалось перенести %s в %s: %s", source, target, exc)
        return None
    log.info("данные игры перенесены из %s в %s", source, target)
    return target


def _backup_config() -> Path | None:
    """Копия конфига перед миграцией: если что-то пойдёт не так, будет откат."""
    source = config_path()
    if not source.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = source.with_name(f"{source.name}.backup-{stamp}")
    try:
        shutil.copy2(source, target)
        return target
    except OSError as exc:
        log.warning("резервная копия конфига не создалась: %s", exc)
        return None


def _migrate_config(cfg: dict, stored_version: object = None) -> bool:
    """Приводит старый конфиг к текущей схеме. True — что-то изменилось."""
    if stored_version is None:
        version = 0
    else:
        try:
            version = int(stored_version)
        except (TypeError, ValueError):
            version = 0
    if version >= CONFIG_VERSION:
        return False
    _backup_config()
    if not isinstance(cfg.get("profiles"), list):
        cfg["profiles"] = []
    for key in ("nickname", "theme", "language", "data_dir", "java_path",
                "selected_version", "selected_loader", "jvm_args", "active_profile"):
        if not isinstance(cfg.get(key), str):
            cfg[key] = str(CONFIG_DEFAULTS.get(key) or "")
    try:
        cfg["ram_mb"] = int(cfg.get("ram_mb") or 2048)
    except (TypeError, ValueError):
        cfg["ram_mb"] = 2048
    if str(cfg.get("theme") or "") not in ("dark", "grey", "light"):
        cfg["theme"] = "dark"
    if str(cfg.get("language") or "") not in i18n.LANGUAGES:
        cfg["language"] = "ru"
    cfg["config_version"] = CONFIG_VERSION
    log.info("конфиг обновлён до схемы %s", CONFIG_VERSION)
    return True


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    _migrate_legacy()
    path = config_path()
    stored_version: object = None
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("конфиг %s не прочитан: %s", path, exc)
            stored = None
        if isinstance(stored, dict):
            # Незнакомые ключи сохраняем: их может добавить новая версия.
            stored_version = stored.get("config_version")
            cfg.update(stored)
    cfg = _map_secrets(cfg, secure.unprotect)
    changed = _migrate_config(cfg, stored_version)
    stored_dir = str(cfg.get("data_dir") or "")
    if is_portable():
        # Переносимая установка главнее старого абсолютного пути из конфига:
        # иначе игра осталась бы в %APPDATA%, а не рядом с exe.
        if not stored_dir or _same_path(stored_dir, state_dir() / "minecraft"):
            portable_dir = str(default_data_dir())
            if portable_dir != stored_dir:
                cfg["data_dir"] = portable_dir
                changed = True
    elif not stored_dir:
        cfg["data_dir"] = str(default_data_dir())
    if changed:
        save_config(cfg)
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
    value = str(cfg.get("data_dir") or "")
    if is_portable() and (not value or _same_path(value, state_dir() / "minecraft")):
        # Старый абсолютный путь из конфига не должен уводить игру в %APPDATA%.
        return app_dir() / "minecraft"
    return Path(value or default_data_dir())


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
