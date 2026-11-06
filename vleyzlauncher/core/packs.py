"""Ресурспаки и шейдеры: установка из Modrinth в папку сборки.

Отключение — перенос файла в подпапку ``_disabled``: Minecraft не читает
вложенные папки, поэтому пак просто пропадает из списка, но остаётся на диске.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import instances, modrinth, net

log = logging.getLogger(__name__)

KINDS = {
    "resourcepack": "resourcepacks",
    "shader": "shaderpacks",
}
DISABLED_SUBDIR = "_disabled"


class PackError(Exception):
    pass


def folder(data_dir: Path, instance_id: str, kind: str) -> Path:
    name = KINDS.get(kind)
    if not name:
        raise PackError(f"Неизвестный тип пака: {kind}")
    return instances.path(data_dir, instance_id) / name


def installed(data_dir: Path, instance_id: str, kind: str) -> list[dict]:
    """Паки в папке сборки: включённые и отключённые."""
    base = folder(data_dir, instance_id, kind)
    result: list[dict] = []
    for enabled, current in ((True, base), (False, base / DISABLED_SUBDIR)):
        if not current.is_dir():
            continue
        for path in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in (".zip", ".mcpack", ".jar"):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            result.append(
                {"name": path.name, "path": path, "enabled": enabled, "size": size}
            )
    return result


def set_enabled(data_dir: Path, instance_id: str, kind: str, name: str, enabled: bool) -> bool:
    base = folder(data_dir, instance_id, kind)
    disabled = base / DISABLED_SUBDIR
    source = (disabled if enabled else base) / name
    if not source.is_file():
        return False
    target_folder = base if enabled else disabled
    target_folder.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(target_folder / name)
        return True
    except OSError:
        return False


def install(
    data_dir: Path,
    instance_id: str,
    kind: str,
    project_id: str,
    game_version: str,
    log_fn=None,
    cancel=None,
) -> dict:
    """Скачивает последнюю подходящую версию пака в папку сборки."""
    log_fn = log_fn or (lambda message: None)
    version = modrinth.pick_pack_version(project_id, game_version)
    if not version:
        raise PackError("Для этой версии игры пак не найден")
    entry = modrinth._entry(version, False)
    target_dir = folder(data_dir, instance_id, kind)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / entry["filename"]
    log_fn(f"Скачиваю {entry['title']}")
    net.download(
        str(entry["url"]),
        target,
        entry.get("sha1"),
        entry.get("size") or None,
        cancel=cancel,
    )
    log_fn(f"Готово: {entry['filename']}")
    return entry
