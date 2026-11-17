from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import instances

MOD_EXTENSIONS = (".jar",)
ENABLED_DIR = "mods"
DISABLED_DIR = "mods-disabled"

log = logging.getLogger(__name__)


def shared_enabled(data_dir: Path) -> Path:
    """Общая папка модов в корне — её видят все сборки."""
    return Path(data_dir) / ENABLED_DIR


def shared_disabled(data_dir: Path) -> Path:
    return Path(data_dir) / DISABLED_DIR


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(os.path, "isjunction", None)
        return bool(checker and checker(path))
    except OSError:
        return False


def _move_files(source: Path, target: Path) -> int:
    """Переносит файлы, не затирая уже существующие."""
    moved = 0
    if not source.is_dir() or _is_link(source):
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if not item.is_file():
            continue
        destination = target / item.name
        if destination.exists():
            continue
        try:
            shutil.move(str(item), str(destination))
            moved += 1
        except OSError:
            continue
    return moved


def _link_folder(link: Path, target: Path) -> bool:
    """Связывает папку сборки с общей: junction на Windows, ссылка как запасной путь."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, timeout=30, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    if link.is_dir():
        return True
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return link.is_dir()


def ensure_shared(data_dir: Path, instance_id: str | None = None) -> Path:
    """Делает общую папку модов в корне и связывает с ней папку сборки.

    Так все моды лежат в одном месте: их видно в лаунчере, можно включать и
    отключать, а игра всё равно находит их в папке сборки.
    """
    root = Path(data_dir)
    shared = shared_enabled(root)
    shared.mkdir(parents=True, exist_ok=True)
    shared_disabled(root).mkdir(parents=True, exist_ok=True)
    if not instance_id:
        return shared

    target = instances.mods_dir(root, instance_id)
    disabled = instances.disabled_dir(root, instance_id)
    if _is_link(target) and _is_link(disabled):
        return shared

    moved = _move_files(target, shared) + _move_files(disabled, shared_disabled(root))
    for folder, link_target in ((target, shared), (disabled, shared_disabled(root))):
        if _is_link(folder):
            continue
        try:
            if folder.is_dir():
                folder.rmdir()
        except OSError:
            pass
        if not _link_folder(folder, link_target):
            log.warning("не удалось связать %s с общей папкой модов", folder)
    if moved:
        log.info("перенёс %s модов в общую папку", moved)
    return shared



def mods_dir(data_dir: Path, instance_id: str | None = None) -> Path:
    """Папка включённых модов.

    Без ``instance_id`` возвращает общую папку — так работают старые
    вызовы и CLI. С идентификатором сборки — папку конкретного инстанса.
    """
    if instance_id:
        return instances.mods_dir(data_dir, instance_id)
    return Path(data_dir) / ENABLED_DIR


def disabled_dir(data_dir: Path, instance_id: str | None = None) -> Path:
    if instance_id:
        return instances.disabled_dir(data_dir, instance_id)
    return Path(data_dir) / DISABLED_DIR


def ensure_dirs(data_dir: Path, instance_id: str | None = None) -> None:
    for folder in (mods_dir(data_dir, instance_id), disabled_dir(data_dir, instance_id)):
        folder.mkdir(parents=True, exist_ok=True)


def _entries(folder: Path, enabled: bool) -> list[dict]:
    result: list[dict] = []
    if not folder.is_dir():
        return result
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in MOD_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        result.append({"name": path.name, "path": path, "enabled": enabled, "size": size})
    return result


def list_mods(data_dir: Path, instance_id: str | None = None) -> list[dict]:
    """Список модов: включённые сверху, отключённые — после."""
    items = (
        _entries(mods_dir(data_dir, instance_id), True)
        + _entries(disabled_dir(data_dir, instance_id), False)
    )
    items.sort(key=lambda item: (not item["enabled"], item["name"].lower()))
    return items


def signature(items: list[dict]) -> tuple:
    """Сигнатура папки: меняется, когда файлы меняются во время работы."""
    return tuple((item["name"], item["enabled"], item["size"]) for item in items)


def set_enabled(
    data_dir: Path,
    name: str,
    enabled: bool,
    instance_id: str | None = None,
) -> bool:
    """Включает или отключает мод, перенося файл между mods и mods-disabled."""
    data_dir = Path(data_dir)
    ensure_dirs(data_dir, instance_id)
    source = (disabled_dir(data_dir, instance_id) if enabled else mods_dir(data_dir, instance_id)) / name
    if not source.is_file():
        return False
    target_folder = mods_dir(data_dir, instance_id) if enabled else disabled_dir(data_dir, instance_id)
    target = target_folder / name
    if target.exists():
        stem, suffix = Path(name).stem, Path(name).suffix
        index = 2
        while target.exists():
            target = target_folder / f"{stem} ({index}){suffix}"
            index += 1
    try:
        shutil.move(str(source), str(target))
    except OSError:
        return False
    return True


def folder_hint(data_dir: Path, instance_id: str | None = None) -> str:
    return str(mods_dir(Path(data_dir), instance_id))
