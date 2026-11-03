from __future__ import annotations

import shutil
from pathlib import Path

MOD_EXTENSIONS = (".jar",)
ENABLED_DIR = "mods"
DISABLED_DIR = "mods-disabled"


def mods_dir(data_dir: Path) -> Path:
    return Path(data_dir) / ENABLED_DIR


def disabled_dir(data_dir: Path) -> Path:
    return Path(data_dir) / DISABLED_DIR


def ensure_dirs(data_dir: Path) -> None:
    for folder in (mods_dir(data_dir), disabled_dir(data_dir)):
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


def list_mods(data_dir: Path) -> list[dict]:
    """Все моды профиля: сначала включённые, внутри — по алфавиту."""
    items = _entries(mods_dir(data_dir), True) + _entries(disabled_dir(data_dir), False)
    items.sort(key=lambda item: (not item["enabled"], item["name"].lower()))
    return items


def signature(items: list[dict]) -> tuple:
    """Отпечаток списка: по нему видно, изменилось ли что-то на диске."""
    return tuple((item["name"], item["enabled"], item["size"]) for item in items)


def set_enabled(data_dir: Path, name: str, enabled: bool) -> bool:
    """Включает или отключает мод, перенося файл между mods и mods-disabled."""
    data_dir = Path(data_dir)
    ensure_dirs(data_dir)
    source = (disabled_dir(data_dir) if enabled else mods_dir(data_dir)) / name
    if not source.is_file():
        return False
    target_folder = mods_dir(data_dir) if enabled else disabled_dir(data_dir)
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


def folder_hint(data_dir: Path) -> str:
    return str(mods_dir(Path(data_dir)))
