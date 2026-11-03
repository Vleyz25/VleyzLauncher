"""Шифрование секретов конфига средствами Windows (DPAPI).

Токены и пароль Ely.by не должны лежать в json открытым текстом: файл
попадает в бэкапы и легко уезжает вместе с папкой лаунчера. DPAPI
привязывает шифр к текущему пользователю Windows, поэтому на другом
компьютере или под другой учёткой секрет не расшифруется.
"""

from __future__ import annotations

import base64
import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

PREFIX = "dpapi:"
ENTROPY = b"VleyzLauncher.config.v1"
_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    """DPAPI есть только на Windows."""
    return sys.platform == "win32"


def _blob(data: bytes):
    # Буфер обязан жить, пока идёт вызов: держим ссылку рядом с блобом.
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _crypt(data: bytes, encrypt: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_Blob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    source, _keep_source = _blob(data)
    entropy, _keep_entropy = _blob(ENTROPY)
    result = _Blob()
    ok = function(
        ctypes.byref(source),
        "Vleyz Launcher" if encrypt else None,
        ctypes.byref(entropy),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(result),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI не смог обработать данные")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, wintypes.HLOCAL))


def protect(value: str) -> str:
    """Зашифровать строку. Пустые значения и не-Windows остаются как есть."""
    if not isinstance(value, str) or not value or value.startswith(PREFIX) or not available():
        return value
    try:
        encrypted = _crypt(value.encode("utf-8"), True)
    except OSError as exc:
        log.warning("не удалось зашифровать секрет: %s", exc)
        return value
    return PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect(value: str) -> str:
    """Расшифровать строку. Если не вышло — вернуть пустую (секрет потерян)."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    if not available():
        return ""
    try:
        decrypted = _crypt(base64.b64decode(value[len(PREFIX):]), False)
        return decrypted.decode("utf-8")
    except (OSError, ValueError) as exc:
        log.warning("не удалось расшифровать секрет: %s", exc)
        return ""
