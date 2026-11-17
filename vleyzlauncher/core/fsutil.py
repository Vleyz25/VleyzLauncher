"""Файловое ядро: атомарная запись и транзакции папки.

Атомарная запись не даёт недописанному файлу заменить рабочий: данные
идут во временный файл рядом, затем os.replace. Транзакция папки качает
файлы в подпапку и переносит их только после успеха, а при ошибке
возвращает прежние версии.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path


def ensure_dir(path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def atomic_write_bytes(path, data: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    )
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return target


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, text.encode(encoding))


class FolderTransaction:
    """Скачиваем в `.vleyz-tx-*` рядом, переносим после успеха."""

    def __init__(self, destination) -> None:
        self.destination = ensure_dir(destination)
        self.root = self.destination / f".vleyz-tx-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.root.mkdir(parents=True, exist_ok=True)
        self._staged: dict[str, Path] = {}
        self._committed: list[tuple[Path, Path | None]] = []

    def stage(self, name: str) -> Path:
        clean = Path(str(name)).name
        target = self.root / clean
        self._staged[clean] = target
        return target

    def commit(self) -> None:
        backup_root = self.root / "backup"
        for name, staged in self._staged.items():
            target = self.destination / name
            backup: Path | None = None
            if target.exists():
                backup_root.mkdir(parents=True, exist_ok=True)
                backup = backup_root / name
                os.replace(target, backup)
            try:
                os.replace(staged, target)
            except OSError:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                raise
            self._committed.append((target, backup))

    def rollback(self) -> None:
        for target, backup in reversed(self._committed):
            try:
                if target.exists():
                    target.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, target)
            except OSError:
                continue
        self._committed.clear()

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "FolderTransaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            try:
                self.commit()
            finally:
                self.close()
        else:
            self.rollback()
            self.close()
        return False
