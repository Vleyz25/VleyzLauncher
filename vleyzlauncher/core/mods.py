from __future__ import annotations

import shutil
from pathlib import Path

from . import instances

MOD_EXTENSIONS = (".jar",)
ENABLED_DIR = "mods"
DISABLED_DIR = "mods-disabled"


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
