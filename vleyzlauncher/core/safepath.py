"""Безопасные пути: имена файлов, распаковка архивов, свободное место.

Всё, что приходит из архива или из сети, считается недоверенным: имя может
содержать ``..``, абсолютный путь, запрещённые Windows-символы или быть
зарезервированным (``CON``). Здесь одно место, где это приводится в порядок.
"""

from __future__ import annotations

import logging
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

log = logging.getLogger(__name__)

MAX_FILES = 20_000
MAX_BYTES = 2 * 1024 ** 3
RESERVE_BYTES = 256 * 1024 ** 2
MAX_NAME = 120

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class PathError(Exception):
    """Базовая ошибка безопасной работы с путями."""


class LimitError(PathError):
    """Архив превысил лимиты распаковки."""


class SpaceError(PathError):
    """Не хватает свободного места."""


def sanitize_name(name: str, fallback: str = "file") -> str:
    """Имя файла или папки, безопасное для Windows."""
    text = _BAD_CHARS.sub("_", str(name or "")).strip(" .")
    if not text:
        return fallback
    stem = text.split(".")[0].upper()
    if stem in _RESERVED:
        text = f"_{text}"
    if len(text) > MAX_NAME:
        dot = text.rfind(".")
        if 0 < dot < len(text) - 1:
            text = text[:MAX_NAME - len(text) + dot] + text[dot:]
        else:
            text = text[:MAX_NAME]
    return text.strip(" .") or fallback


def safe_relative(path: str) -> PurePosixPath | None:
    """Относительный путь внутри папки назначения или None, если он опасен."""
    text = str(path or "").replace("\\", "/").strip()
    if not text:
        return None
    candidate = PurePosixPath(text)
    if candidate.is_absolute():
        return None
    parts = [part for part in candidate.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    cleaned = [sanitize_name(part) for part in parts]
    return PurePosixPath(*cleaned)


def is_inside(target: Path, root: Path) -> bool:
    """Лежит ли путь внутри корня после нормализации."""
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def free_space(path: Path) -> int:
    target = Path(path)
    while not target.exists() and target.parent != target:
        target = target.parent
    try:
        return shutil.disk_usage(target).free
    except OSError:
        return 0


def human_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def require_space(path: Path, need: int, reserve: int = RESERVE_BYTES) -> None:
    """Проверяет, что под установку хватит места с запасом."""
    if need <= 0:
        return
    free = free_space(path)
    if not free:
        return
    if free < need + reserve:
        raise SpaceError(
            f"Не хватает места: нужно {human_bytes(need + reserve)}, "
            f"свободно {human_bytes(free)}"
        )


def extract_zip(
    archive: zipfile.ZipFile,
    members: Iterable[str],
    target: Path,
    strip: tuple[str, ...] = (),
    max_files: int = MAX_FILES,
    max_bytes: int = MAX_BYTES,
) -> dict:
    """Распаковывает выбранные файлы архива внутрь ``target``.

    Возвращает отчёт: сколько файлов записано и сколько пропущено как опасные.
    """
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    total = 0
    for name in members:
        try:
            info = archive.getinfo(name)
        except KeyError:
            skipped += 1
            continue
        if info.is_dir():
            continue
        relative = name
        for prefix in strip:
            if name.startswith(prefix):
                relative = name[len(prefix):]
                break
        safe = safe_relative(relative)
        if safe is None:
            skipped += 1
            log.warning("пропускаю опасный путь в архиве: %r", name)
            continue
        destination = target / safe
        if not is_inside(destination, target):
            skipped += 1
            continue
        written += 1
        if written > max_files:
            raise LimitError(f"В архиве больше {max_files} файлов")
        total += int(info.file_size or 0)
        if total > max_bytes:
            raise LimitError(f"Архив распаковывается больше чем на {human_bytes(max_bytes)}")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, open(destination, "wb") as sink:
                shutil.copyfileobj(source, sink)
        except (OSError, KeyError) as exc:
            log.warning("не удалось распаковать %s: %s", name, exc)
            skipped += 1
    return {"written": written, "skipped": skipped, "bytes": total}
