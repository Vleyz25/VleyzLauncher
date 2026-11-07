"""Бэкапы миров: zip папки ``saves`` с ротацией и восстановлением.

Архив ложится в ``<instance>/backups``. Перед восстановлением текущие миры
переименовываются в ``saves-before-<время>`` — если что-то пойдёт не так,
старые данные останутся на диске.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from . import instances, safepath

log = logging.getLogger(__name__)

BACKUP_DIR = "backups"
KEEP_DEFAULT = 5
MAX_FILE_BYTES = 512 * 1024 * 1024


class BackupError(Exception):
    pass


def backup_dir(data_dir: Path, instance_id: str) -> Path:
    return instances.path(data_dir, instance_id) / BACKUP_DIR


def list_backups(data_dir: Path, instance_id: str) -> list[dict]:
    base = backup_dir(data_dir, instance_id)
    if not base.is_dir():
        return []
    result: list[dict] = []
    for path in base.glob("*.zip"):
        try:
            stat = path.stat()
        except OSError:
            continue
        result.append(
            {
                "name": path.name,
                "path": path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "created": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m %H:%M"),
            }
        )
    result.sort(key=lambda item: item["mtime"], reverse=True)
    return result


def _rotate(base: Path, keep: int) -> list[str]:
    removed: list[str] = []
    items = sorted(base.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in items[max(1, int(keep)):]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            log.warning("бэкап %s не удалился", path)
    return removed


def create(
    data_dir: Path,
    instance_id: str,
    label: str = "",
    keep: int = KEEP_DEFAULT,
    log_fn=None,
) -> dict | None:
    """Пакует папку saves. Возвращает описание архива или None, если миров нет."""
    log_fn = log_fn or (lambda message: None)
    saves = instances.saves_dir(data_dir, instance_id)
    if not saves.is_dir():
        return None
    worlds = [path for path in saves.iterdir() if path.is_dir()]
    if not worlds:
        return None

    base = backup_dir(data_dir, instance_id)
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = base / f"saves-{stamp}{suffix}.zip"

    total = 0
    count = 0
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(saves.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if total + size > MAX_FILE_BYTES:
                    log.warning("архив упёрся в лимит %s МБ", MAX_FILE_BYTES // 1048576)
                    break
                archive.write(path, str(path.relative_to(saves)))
                total += size
                count += 1
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Не удалось создать бэкап: {exc}") from exc

    removed = _rotate(base, keep)
    if removed:
        log_fn(f"Удалены старые бэкапы: {', '.join(removed)}")
    log_fn(f"Бэкап готов: {target.name} ({count} файлов)")
    return {
        "name": target.name,
        "path": target,
        "size": total,
        "files": count,
        "worlds": len(worlds),
        "created": datetime.now().strftime("%d.%m %H:%M"),
    }


def restore(data_dir: Path, instance_id: str, name: str) -> Path:
    """Распаковывает бэкап в папку saves, сохраняя текущие миры рядом."""
    base = backup_dir(data_dir, instance_id)
    archive_path = base / Path(str(name)).name
    if not archive_path.is_file():
        raise BackupError("Бэкап не найден")
    saves = instances.saves_dir(data_dir, instance_id)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if saves.is_dir() and any(saves.iterdir()):
        old = instances.path(data_dir, instance_id) / f"saves-before-{stamp}"
        try:
            saves.replace(old)
        except OSError as exc:
            raise BackupError(f"Не удалось убрать текущие миры: {exc}") from exc
    saves.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            safepath.extract_zip(archive, archive.namelist(), saves)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError(f"Архив не распаковался: {exc}") from exc
    return saves


def delete(data_dir: Path, instance_id: str, name: str) -> bool:
    target = backup_dir(data_dir, instance_id) / Path(str(name)).name
    if not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False
