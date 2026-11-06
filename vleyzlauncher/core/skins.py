"""Скины: голова и полный скин игрока по нику.

Картинки берутся с публичного отображения скинов (mc-heads.net) и кэшируются
в ``<data_dir>/cache/skins``. Ничего не загружается на серверы: лаунчер только
показывает скин и сохраняет картинку рядом, если она нужна.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import net

log = logging.getLogger(__name__)

AVATAR_URL = "https://mc-heads.net/avatar/{nick}/{size}"
SKIN_URL = "https://mc-heads.net/skin/{nick}"
CACHE_DIR = "cache/skins"

_NICK = re.compile(r"^[A-Za-z0-9_]{1,16}$")


class SkinError(Exception):
    pass


def _cache_dir(data_dir: Path) -> Path:
    target = Path(data_dir) / CACHE_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _fetch(url: str, dest: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise SkinError(f"Скин не найден ({exc.code})") from exc
    except Exception as exc:  # noqa: BLE001 - сеть бывает любой
        raise SkinError(f"Не удалось получить скин: {exc}") from exc
    if len(body) < 64:
        raise SkinError("Сервер вернул пустую картинку")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return dest


def valid_nick(nick: str) -> bool:
    return bool(_NICK.match(str(nick or "")))


def head(data_dir: Path, nick: str, size: int = 128, refresh: bool = False) -> Path:
    """Превью головы игрока (png), кэшируется по нику и размеру."""
    nick = str(nick or "").strip()
    if not valid_nick(nick):
        raise SkinError("Ник может содержать только латиницу, цифры и подчёркивание")
    size = max(32, min(512, int(size)))
    target = _cache_dir(data_dir) / f"{nick.lower()}-{size}.png"
    if target.is_file() and not refresh and target.stat().st_size > 64:
        return target
    return _fetch(AVATAR_URL.format(nick=nick, size=size), target)


def skin(data_dir: Path, nick: str, refresh: bool = False) -> Path:
    """Полный скин игрока (png с прозрачностью), кэшируется по нику."""
    nick = str(nick or "").strip()
    if not valid_nick(nick):
        raise SkinError("Ник может содержать только латиницу, цифры и подчёркивание")
    target = _cache_dir(data_dir) / f"{nick.lower()}-skin.png"
    if target.is_file() and not refresh and target.stat().st_size > 64:
        return target
    return _fetch(SKIN_URL.format(nick=nick), target)


def save_copy(source: Path, destination: Path) -> Path:
    """Копирует картинку скина туда, куда указал пользователь."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(Path(source).read_bytes())
    return destination
