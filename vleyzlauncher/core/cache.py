"""Дисковый кэш ответов сети: SQLite, время жизни и очистка.

Нужен, чтобы каталог модов открывался мгновенно и переживал перезапуск
лаунчера. Ключ — строка, значение — JSON. Просроченные записи удаляются
при чтении, отдельного обслуживания не требуется.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

DEFAULT_TTL = 300.0


class DiskCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else paths.state_dir() / "cache.db"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "key TEXT PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL)"
        )
        return connection

    @contextmanager
    def _open(self):
        """Соединение закрывается всегда: иначе Windows держит файл кэша."""
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get(self, key: str, ttl: float = DEFAULT_TTL):
        try:
            with self._open() as connection:
                row = connection.execute(
                    "SELECT ts, data FROM cache WHERE key = ?", (str(key),)
                ).fetchone()
                if row is None:
                    return None
                stamp, data = row
                if ttl > 0 and time.time() - float(stamp) > ttl:
                    connection.execute("DELETE FROM cache WHERE key = ?", (str(key),))
                    return None
                return json.loads(data)
        except (OSError, sqlite3.Error, ValueError) as exc:
            log.debug("кэш не прочитан: %s", exc)
            return None

    def set(self, key: str, value) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            log.debug("кэш не записан: %s", exc)
            return
        try:
            with self._open() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO cache (key, ts, data) VALUES (?, ?, ?)",
                    (str(key), time.time(), payload),
                )
        except (OSError, sqlite3.Error) as exc:
            log.debug("кэш не записан: %s", exc)

    def clear(self) -> None:
        try:
            with self._open() as connection:
                connection.execute("DELETE FROM cache")
        except (OSError, sqlite3.Error) as exc:
            log.debug("кэш не очищен: %s", exc)

    def prune(self, ttl: float = DEFAULT_TTL) -> int:
        """Удаляет просроченные записи. Возвращает число удалённых."""
        try:
            with self._open() as connection:
                cursor = connection.execute(
                    "DELETE FROM cache WHERE ts < ?", (time.time() - max(0.0, ttl),)
                )
                return int(cursor.rowcount or 0)
        except (OSError, sqlite3.Error) as exc:
            log.debug("кэш не почищен: %s", exc)
            return 0
