"""Очистка мусора, который лаунчер создал сам.

Удаляются только те файлы, которые можно скачать или создать заново:
установщики Forge/NeoForge, недокачанные ``.part``, кэш ответов Modrinth и
старые логи. Папки ``mods``, ``saves``, ``config``, ``resourcepacks`` и
``shaderpacks`` не трогаются никогда — это данные игрока.
"""

from __future__ import annotations

import shutil
from pathlib import Path

INSTALLERS = "installers"
CACHE = "cache"
LOGS = "logs"
PARTIAL_SUFFIX = ".part"

PROTECTED = ("mods", "saves", "config", "resourcepacks", "shaderpacks", "instances")

TITLES = {
    "installers": "Установщики Forge/NeoForge",
    "partial": "Недокачанные файлы",
    "cache": "Кэш каталога",
    "logs": "Старые логи",
}

LOG_LIMIT = 20


class CleanupError(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _protected(path: Path, root: Path, kind: str) -> bool:
    """Файл лежит в папке с данными игрока — его не удаляем."""
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except (ValueError, OSError):
        return True
    if kind in ("installers", "cache"):
        return False
    return any(part in PROTECTED for part in parts[:-1])


def _files(data_dir: Path, kind: str) -> list[Path]:
    root = Path(data_dir)
    result: list[Path] = []
    if kind == "installers":
        folder = root / INSTALLERS
        if folder.is_dir():
            result = [item for item in folder.glob("*.jar") if item.is_file()]
    elif kind == "cache":
        folder = root / CACHE
        if folder.is_dir():
            result = [item for item in folder.rglob("*") if item.is_file()]
    elif kind == "logs":
        folder = root / LOGS
        if folder.is_dir():
            files = [item for item in folder.glob("*.log") if item.is_file()]
            files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            result = files[LOG_LIMIT:]
    elif kind == "partial":
        for item in root.rglob(f"*{PARTIAL_SUFFIX}"):
            if item.is_file() and not _protected(item, root, "partial"):
                result.append(item)
    else:
        raise CleanupError(f"неизвестная категория: {kind}")
    return [item for item in result if _inside(item, root) and not _protected(item, root, kind)]


def scan(data_dir: Path) -> list[dict]:
    """Что можно удалить: категория, число файлов и общий размер."""
    root = Path(data_dir)
    rows: list[dict] = []
    for kind, title in TITLES.items():
        try:
            files = _files(root, kind)
        except OSError:
            continue
        size = 0
        for item in files:
            try:
                size += item.stat().st_size
            except OSError:
                continue
        rows.append({"key": kind, "title": title, "files": len(files), "size": size})
    return rows


def clean(data_dir: Path, keys: tuple[str, ...] | list[str] | None = None,
          log=None) -> dict:
    """Удаляет выбранные категории. Возвращает освобождённое место и число файлов."""
    root = Path(data_dir)
    log = log or (lambda message: None)
    wanted = list(keys or TITLES.keys())
    unknown = [key for key in wanted if key not in TITLES]
    if unknown:
        raise CleanupError(f"неизвестная категория: {', '.join(unknown)}")

    freed = 0
    removed = 0
    for kind in wanted:
        for item in _files(root, kind):
            try:
                size = item.stat().st_size
                item.unlink()
            except OSError:
                continue
            freed += size
            removed += 1
        if kind == "cache":
            folder = root / CACHE
            if folder.is_dir():
                for child in folder.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
    log(f"Очистка: файлов {removed}, освобождено {freed} байт")
    return {"removed": removed, "freed": freed, "categories": wanted}
