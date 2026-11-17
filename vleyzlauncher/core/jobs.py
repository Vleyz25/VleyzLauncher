"""Фоновые задачи: ограничение параллельности, дедупликация, результат.

Одна и та же задача не запускается дважды, а число одновременных
ограничено. Ошибка не теряется: она лежит в результате и передаётся в
on_done вторым аргументом.
"""

from __future__ import annotations

import threading
from typing import Callable


class JobRunner:
    def __init__(self, workers: int = 4) -> None:
        self._gate = threading.Semaphore(max(1, int(workers)))
        self._lock = threading.Lock()
        self._running: dict[str, threading.Thread] = {}
        self._results: dict[str, dict] = {}

    def submit(self, name: str, func: Callable,
               on_done: Callable | None = None) -> bool:
        """True — задача принята. False — уже идёт или нет свободного слота."""
        if not self._gate.acquire(blocking=False):
            return False
        with self._lock:
            if name in self._running:
                self._gate.release()
                return False
            self._running[name] = threading.current_thread()

        def run() -> None:
            result = None
            error: Exception | None = None
            try:
                result = func()
            except Exception as exc:  # noqa: BLE001 - ошибку показываем вызывающему
                error = exc
            with self._lock:
                self._running.pop(name, None)
                self._results[name] = {"result": result, "error": error}
            self._gate.release()
            if on_done is not None:
                try:
                    on_done(result, error)
                except Exception:  # noqa: BLE001 - колбэк не должен ронять раннер
                    pass

        thread = threading.Thread(target=run, daemon=True, name=f"job-{name}")
        with self._lock:
            self._running[name] = thread
        thread.start()
        return True

    def is_running(self, name: str) -> bool:
        with self._lock:
            return name in self._running

    def result(self, name: str) -> dict | None:
        with self._lock:
            return self._results.get(name)

    def wait(self, name: str, timeout: float = 10.0) -> dict | None:
        with self._lock:
            thread = self._running.get(name)
        if thread is not None:
            thread.join(timeout)
        return self.result(name)

    def clear(self) -> None:
        with self._lock:
            self._results.clear()
