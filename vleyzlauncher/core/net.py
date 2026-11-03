from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Callable

USER_AGENT = "VleyzLauncher/1.0 (Minecraft launcher; official Mojang resources)"
CHUNK = 256 * 1024


class DownloadError(Exception):
    pass


class ChecksumError(DownloadError):
    pass


class DownloadCancelled(Exception):
    pass


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )


def fetch_json(url: str, timeout: int = 30, retries: int = 4):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - сеть бывает любой
            last = exc
            if attempt < retries - 1:
                time.sleep(1.2 * (attempt + 1))
    raise DownloadError(f"Не удалось получить {url}: {last}")


def fetch_text(url: str, timeout: int = 30, retries: int = 4) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(1.2 * (attempt + 1))
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
) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last: Exception | None = None

    for attempt in range(retries):
        got = 0
        try:
            digest = hashlib.sha1()
            with urllib.request.urlopen(_request(url), timeout=timeout) as resp, open(tmp, "wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    got += len(block)
                    if on_bytes is not None:
                        on_bytes(len(block))
            if sha1 and digest.hexdigest() != sha1:
                raise ChecksumError(f"Проверка SHA1 не прошла: {dest.name}")
            if size is not None and size != got:
                raise ChecksumError(f"Размер не совпал: {dest.name} ({got} != {size})")
            os.replace(tmp, dest)
            return
        except DownloadCancelled:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if on_bytes is not None and got:
                on_bytes(-got)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
    raise DownloadError(f"Не удалось скачать {url}: {last}")
