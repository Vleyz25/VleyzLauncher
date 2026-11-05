"""Изолированные папки сборок.

Каждая установленная версия получает собственный каталог в
``<data_dir>/instances/<version_id>``. Внутри живут ``mods``, ``saves``,
``config`` и остальные файлы, которые Minecraft пишет в game directory.
Общие ``versions``, ``libraries``, ``assets`` и ``installers`` остаются в
корне data_dir, поэтому установка не дублируется.

Для старых установок при первом создании инстанса общие папки один раз
копируются внутрь него. Исходные данные не удаляются.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

INSTANCES_DIR = "instances"

INSTANCE_FOLDERS = (
    "mods",
    "mods-disabled",
    "saves",
    "config",
    "resourcepacks",
    "shaderpacks",
    "screenshots",
    "logs",
    "crash-reports",
)

LEGACY_FOLDERS = (
    "mods",
    "mods-disabled",
    "saves",
    "config",
    "resourcepacks",
    "shaderpacks",
    "screenshots",
)

LEGACY_FILES = (
    "options.txt",
    "optionsof.txt",
    "optionsshaders.txt",
    "servers.dat",
    "usercache.json",
    "usernamecache.json",
)

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(version_id: str) -> str:
    """Имя каталога, безопасное для Windows."""
    value = _BAD_CHARS.sub("_", str(version_id or "")).strip(" .")
    return value or "instance"


def root(data_dir: Path) -> Path:
    return Path(data_dir) / INSTANCES_DIR


def path(data_dir: Path, instance_id: str) -> Path:
    return root(data_dir) / safe_name(instance_id)


def exists(data_dir: Path, instance_id: str) -> bool:
    return path(data_dir, instance_id).is_dir()


def _copy_legacy(data_dir: Path, target: Path) -> None:
    """Копирует общие папки старой установки в новый инстанс."""
    data_dir = Path(data_dir)
    copied: list[str] = []
    for name in LEGACY_FOLDERS:
        source = data_dir / name
        destination = target / name
        if not source.is_dir() or not any(source.iterdir()):
            continue
        try:
            shutil.copytree(source, destination, dirs_exist_ok=True)
            copied.append(name)
        except OSError as exc:
            log.warning("не удалось скопировать %s в инстанс: %s", source, exc)
    for name in LEGACY_FILES:
        source = data_dir / name
        destination = target / name
        if not source.is_file() or destination.exists():
            continue
        try:
            shutil.copy2(source, destination)
            copied.append(name)
        except OSError as exc:
            log.warning("не удалось скопировать %s в инстанс: %s", source, exc)
    if copied:
        log.info("инстанс %s: перенесены общие данные %s", target.name, ", ".join(copied))


def ensure(data_dir: Path, instance_id: str, migrate: bool = True) -> Path:
    """Создаёт каталог сборки и возвращает его путь."""
    target = path(data_dir, instance_id)
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    for name in INSTANCE_FOLDERS:
        (target / name).mkdir(parents=True, exist_ok=True)
    if created and migrate:
        _copy_legacy(Path(data_dir), target)
    try:
        (target / ".vleyz-instance").write_text(str(instance_id), encoding="utf-8")
    except OSError:
        pass
    return target


def game_dir(data_dir: Path, instance_id: str) -> Path:
    """Game directory для запуска сборки."""
    return ensure(data_dir, instance_id)


def mods_dir(data_dir: Path, instance_id: str) -> Path:
    return path(data_dir, instance_id) / "mods"


def disabled_dir(data_dir: Path, instance_id: str) -> Path:
    return path(data_dir, instance_id) / "mods-disabled"


def saves_dir(data_dir: Path, instance_id: str) -> Path:
    return path(data_dir, instance_id) / "saves"


def config_dir(data_dir: Path, instance_id: str) -> Path:
    return path(data_dir, instance_id) / "config"


def logs_dir(data_dir: Path, instance_id: str) -> Path:
    return path(data_dir, instance_id) / "logs"


def list_instances(data_dir: Path) -> list[str]:
    base = root(data_dir)
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def size(data_dir: Path, instance_id: str) -> int:
    total = 0
    base = path(data_dir, instance_id)
    if not base.is_dir():
        return 0
    for item in base.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total
