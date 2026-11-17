"""Шина событий ядра: модули не знают друг о друге напрямую.

Обработчик не должен ронять вызвавшего: исключения ловятся и пишутся в
лог. Подписка возвращает функцию отписки.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, name: str, handler: Callable) -> Callable[[], None]:
        with self._lock:
            self._handlers.setdefault(name, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                items = self._handlers.get(name) or []
                if handler in items:
                    items.remove(handler)

        return unsubscribe

    def emit(self, name: str, **payload) -> int:
        """Возвращает число обработчиков, которым событие ушло."""
        with self._lock:
            handlers = list(self._handlers.get(name) or [])
        for handler in handlers:
            try:
                handler(**payload)
            except Exception:  # noqa: BLE001 - один обработчик не ломает остальные
                log.exception("обработчик события %s упал", name)
        return len(handlers)

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


bus = EventBus()
