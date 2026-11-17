"""Скины: голова и полный скин игрока по нику.

Картинки берутся с публичного отображения скинов (mc-heads.net) и кэшируются
в ``<data_dir>/cache/skins``. Ничего не загружается на серверы: лаунчер только
показывает скин и сохраняет картинку рядом, если она нужна.

Скин можно взять не только по нику: по прямой ссылке на PNG с любого сайта
скинов или из файла на диске. Такой скин проверяется как настоящий 64×64 и
ставится на аккаунт Microsoft (плащи Mojang ставить не разрешает — их выдают
только за заслуги или рисуют моды).
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import net

log = logging.getLogger(__name__)

AVATAR_URL = "https://mc-heads.net/avatar/{nick}/{size}"
SKIN_URL = "https://mc-heads.net/skin/{nick}"
BODY_URL = "https://mc-heads.net/body/{nick}/{size}"
UPLOAD_URL = "https://api.minecraftservices.com/minecraft/profile/skins"
CACHE_DIR = "cache/skins"
MAX_BYTES = 2 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
VARIANTS = ("classic", "slim")
SKIN_SIZES = ((64, 64), (64, 32))

_NICK = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_BLOCKED_HOSTS = ("localhost", "127.", "0.0.0.0", "[::1]", ".local")


class SkinError(Exception):
    pass


def _cache_dir(data_dir: Path) -> Path:
    target = Path(data_dir) / CACHE_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _read_limited(response) -> bytes:
    body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise SkinError("Файл больше 2 МБ — это не скин")
    return body


def _fetch(url: str, dest: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = _read_limited(response)
    except urllib.error.HTTPError as exc:
        raise SkinError(f"Скин не найден ({exc.code})") from exc
    except SkinError:
        raise
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


def png_size(path: Path) -> tuple[int, int]:
    """Размер PNG из заголовка, без сторонних библиотек."""
    try:
        raw = Path(path).read_bytes()[:24]
    except OSError as exc:
        raise SkinError(f"Файл не читается: {exc}") from exc
    if len(raw) < 24 or not raw.startswith(PNG_SIGNATURE):
        raise SkinError("Это не PNG-картинка")
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    return width, height


def validate_skin(path: Path) -> tuple[int, int]:
    """Скин для Minecraft — ровно 64×64 (или старый 64×32)."""
    width, height = png_size(path)
    if (width, height) not in SKIN_SIZES:
        raise SkinError(
            f"Нужен скин 64×64, а в файле {width}×{height}. "
            "Скачайте PNG со страницы скина, а не превью."
        )
    return width, height


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


def body(data_dir: Path, nick: str, size: int = 192, refresh: bool = False) -> Path:
    """Фигурка игрока целиком — с плащом, если он у аккаунта есть."""
    nick = str(nick or "").strip()
    if not valid_nick(nick):
        raise SkinError("Ник может содержать только латиницу, цифры и подчёркивание")
    size = max(64, min(512, int(size)))
    target = _cache_dir(data_dir) / f"{nick.lower()}-body-{size}.png"
    if target.is_file() and not refresh and target.stat().st_size > 64:
        return target
    return _fetch(BODY_URL.format(nick=nick, size=size), target)


def skin(data_dir: Path, nick: str, refresh: bool = False) -> Path:
    """Полный скин игрока (png с прозрачностью), кэшируется по нику."""
    nick = str(nick or "").strip()
    if not valid_nick(nick):
        raise SkinError("Ник может содержать только латиницу, цифры и подчёркивание")
    target = _cache_dir(data_dir) / f"{nick.lower()}-skin.png"
    if target.is_file() and not refresh and target.stat().st_size > 64:
        return target
    return _fetch(SKIN_URL.format(nick=nick), target)


def _check_url(url: str) -> str:
    url = str(url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SkinError("Нужна прямая ссылка http(s) на PNG-файл скина")
    host = parsed.hostname or ""
    if any(host.startswith(item) or host.endswith(item) for item in _BLOCKED_HOSTS):
        raise SkinError("Ссылки на локальную сеть не поддерживаются")
    return url


def from_url(data_dir: Path, url: str) -> Path:
    """Скачать скин по прямой ссылке с сайта скинов и проверить размер."""
    url = _check_url(url)
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    target = _cache_dir(data_dir) / f"url-{name}.png"
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body_bytes = _read_limited(response)
    except urllib.error.HTTPError as exc:
        raise SkinError(f"Сайт ответил ошибкой {exc.code}") from exc
    except SkinError:
        raise
    except Exception as exc:  # noqa: BLE001 - сеть бывает любой
        raise SkinError(f"Не удалось скачать: {exc}") from exc
    if not body_bytes.startswith(PNG_SIGNATURE):
        raise SkinError("По ссылке не PNG-файл, а страница сайта")
    tmp = target.with_suffix(".part")
    tmp.write_bytes(body_bytes)
    tmp.replace(target)
    validate_skin(target)
    log.info("скин скачан по ссылке: %s", url)
    return target


def from_file(source: Path, data_dir: Path) -> Path:
    """Взять скин из файла на диске и положить в кэш лаунчера."""
    source = Path(source)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise SkinError(f"Файл не читается: {exc}") from exc
    if len(raw) > MAX_BYTES:
        raise SkinError("Файл больше 2 МБ — это не скин")
    name = hashlib.sha1(raw).hexdigest()[:12]
    target = _cache_dir(data_dir) / f"file-{name}.png"
    if not target.is_file():
        target.write_bytes(raw)
    validate_skin(target)
    return target


def upload(token: str, path: Path, variant: str = "classic") -> None:
    """Поставить скин на аккаунт Microsoft через официальный API Mojang."""
    token = str(token or "").strip()
    if not token:
        raise SkinError("Нужен вход Microsoft — офлайн-нику скин не поставить")
    path = Path(path)
    validate_skin(path)
    variant = "slim" if str(variant).lower() == "slim" else "classic"
    boundary = f"----Vleyz{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="variant"\r\n\r\n'
        f"{variant}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="skin.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + path.read_bytes() + tail
    request = urllib.request.Request(
        UPLOAD_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": net.USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:160]
        except Exception:  # noqa: BLE001 - тело ошибки необязательно
            pass
        if exc.code == 401:
            raise SkinError("Сессия Microsoft истекла — войдите заново") from exc
        if exc.code == 400:
            raise SkinError("Mojang отклонил скин: проверьте размер 64×64 и тип") from exc
        raise SkinError(f"Не удалось поставить скин ({exc.code}) {detail}".strip()) from exc
    except Exception as exc:  # noqa: BLE001 - сеть бывает любой
        raise SkinError(f"Не удалось поставить скин: {exc}") from exc


def save_copy(source: Path, destination: Path) -> Path:
    """Копирует картинку скина туда, куда указал пользователь."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(Path(source).read_bytes())
    return destination
