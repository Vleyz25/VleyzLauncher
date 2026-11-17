from __future__ import annotations

import hashlib
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .. import APP_VERSION

USER_AGENT = f"VleyzLauncher/{APP_VERSION} (Minecraft launcher; official Mojang resources)"
CHUNK = 256 * 1024


class DownloadError(Exception):
    pass


class ChecksumError(DownloadError):
    pass


class DownloadCancelled(Exception):
    pass


RETRY_BASE = 0.8
RETRY_MAX = 8.0
_NO_RETRY = {400, 401, 403, 404, 405, 410}

_UNSET = object()
_SSL_CONTEXT: object = _UNSET


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code not in _NO_RETRY
    return True


def _retry_delay(attempt: int, exc: Exception | None = None) -> float:
    """Пауза перед повтором: Retry-After сервера или рост с джиттером."""
    if isinstance(exc, urllib.error.HTTPError) and exc.headers:
        header = exc.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, min(60.0, float(header)))
            except (TypeError, ValueError):
                pass
    base = min(RETRY_MAX, RETRY_BASE * (2 ** max(0, attempt)))
    return min(RETRY_MAX, base * (0.7 + random.random() * 0.6))


def _ca_file() -> str | None:
    """Ищет cacert.pem: рядом с exe, в распакованной сборке, в certifi."""
    roots = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        candidate = root / "certifi" / "cacert.pem"
        if candidate.is_file():
            return str(candidate)
    try:
        import certifi
    except ImportError:
        return None
    path = certifi.where()
    return path if os.path.isfile(path) else None


def _ssl_context() -> ssl.SSLContext | None:
    """Контекст TLS с корневыми сертификатами certifi, если они есть в сборке.

    На Windows 7 хранилище корневых сертификатов бывает устаревшим, и HTTPS к
    Mojang падает. certifi идёт в комплекте, поэтому проверку делаем по нему.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not _UNSET:
        return _SSL_CONTEXT
    cafile = _ca_file()
    if not cafile:
        _SSL_CONTEXT = None
        return _SSL_CONTEXT
    try:
        _SSL_CONTEXT = ssl.create_default_context(cafile=cafile)
    except (OSError, ssl.SSLError):
        _SSL_CONTEXT = None
    return _SSL_CONTEXT


def _open(request: urllib.request.Request, timeout: int):
    """urlopen с контекстом certifi, если он есть в сборке."""
    context = _ssl_context()
    if context is None:
        return urllib.request.urlopen(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _request(url: str, headers: dict | None = None) -> urllib.request.Request:
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if headers:
        merged.update(headers)
    return urllib.request.Request(url, headers=merged)


def fetch_json(url: str, timeout: int = 30, retries: int = 4):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _open(_request(url), timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - сеть бывает любой
            last = exc
            if not _retryable(exc):
                break
            if attempt < retries - 1:
                time.sleep(_retry_delay(attempt, exc))
    raise DownloadError(f"Не удалось получить {url}: {last}")


def fetch_text(url: str, timeout: int = 30, retries: int = 4) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _open(_request(url), timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _retryable(exc):
                break
            if attempt < retries - 1:
                time.sleep(_retry_delay(attempt, exc))
    raise DownloadError(f"Не удалось получить {url}: {last}")


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha512_file(path: Path) -> str:
    digest = hashlib.sha512()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_is_valid(path: Path, sha1: str | None, size: int | None) -> bool:
    if not path.is_file():
        return False
    try:
        if size is not None and path.stat().st_size != size:
            return False
        if sha1:
            return sha1_file(path) == sha1
        return True
    except OSError:
        return False


def download(
    url: str,
    dest: Path,
    sha1: str | None = None,
    size: int | None = None,
    retries: int = 4,
    timeout: int = 60,
    on_bytes: Callable[[int], None] | None = None,
    cancel=None,
    resume: bool = True,
    sha512: str | None = None,
) -> None:
    """Скачать файл с докачкой.

    `.part` остаётся на диске между попытками и запусками: следующий вызов
    продолжит с места обрыва, если сервер поддерживает Range. Прогресс
    учитывает уже скачанный кусок, чтобы проценты не прыгали назад.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if not resume:
        tmp.unlink(missing_ok=True)

    last: Exception | None = None
    counted = 0

    def report(count: int) -> None:
        nonlocal counted
        counted += count
        if on_bytes is not None:
            on_bytes(count)

    for attempt in range(retries):
        offset = tmp.stat().st_size if resume and tmp.is_file() else 0
        if size is not None and offset > size:
            report(-counted)
            tmp.unlink(missing_ok=True)
            offset = 0
        if offset and counted == 0:
            # После перезапуска прогресс начинается с нуля — учитываем .part.
            report(offset)

        headers = {"Range": f"bytes={offset}-"} if offset else None
        try:
            with _open(_request(url, headers), timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if offset and status != 206:
                    # Сервер проигнорировал Range: качаем файл заново.
                    report(-counted)
                    tmp.unlink(missing_ok=True)
                    offset = 0
                mode = "ab" if offset else "wb"
                with open(tmp, mode) as handle:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled()
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        handle.write(block)
                        report(len(block))

            total = tmp.stat().st_size
            if size is not None and total != size:
                raise ChecksumError(f"Размер не совпал: {dest.name} ({total} != {size})")
            if sha1 and sha1_file(tmp) != sha1:
                raise ChecksumError(f"Проверка SHA1 не прошла: {dest.name}")
            if sha512 and sha512_file(tmp) != sha512:
                raise ChecksumError(f"Проверка SHA512 не прошла: {dest.name}")
            os.replace(tmp, dest)
            return
        except DownloadCancelled:
            tmp.unlink(missing_ok=True)
            report(-counted)
            raise
        except Exception as exc:  # noqa: BLE001 - сеть бывает любой
            last = exc
            reset = isinstance(exc, ChecksumError)
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416:
                reset = True  # Range за пределами файла — начинаем заново
            if reset:
                tmp.unlink(missing_ok=True)
                report(-counted)
            if not _retryable(exc):
                break
            if attempt < retries - 1:
                time.sleep(_retry_delay(attempt, exc))
    raise DownloadError(f"Не удалось скачать {url}: {last}")
