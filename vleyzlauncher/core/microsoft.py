"""Аккаунт Microsoft — официальный вход, как в лаунчере Mojang.

Поток device code: лаунчер показывает короткий код, пользователь вводит его
на microsoft.com/link, после подтверждения идёт цепочка
Microsoft → Xbox Live → XSTS → Minecraft Services → профиль. Серверы
с online-mode принимают такой аккаунт, скины и плащи подтягиваются сами.

Токены лежат в локальном конфиге: access-токен Minecraft (на сутки) и
refresh-токен Microsoft — пароль нигде не хранится.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "04f0c124-f2bc-4f59-8241-bf6df9866bbd"  # публичный клиент Minecraft
SCOPE = "XboxLive.signin offline_access"
DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
TIMEOUT = 25
REFRESH_MARGIN = 300  # за сколько секунд до конца токена обновляем заранее

XERR = {
    "2148916233": "У аккаунта Microsoft нет профиля Xbox — создайте его на xbox.com",
    "2148916235": "Xbox Live недоступен в стране аккаунта",
    "2148916236": "Аккаунт Xbox Live заблокирован",
    "2148916237": "Аккаунт Xbox Live заблокирован",
    "2148916227": "Аккаунт заблокирован в Xbox Live",
    "2148916238": "Детский аккаунт: нужен взрослый участник семейной группы",
}


class AuthError(Exception):
    """Ошибка входа Microsoft с понятным текстом для пользователя."""


# ------------------------------------------------------------ HTTP протокол

def _open(url: str, data: bytes | None, headers: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            return int(resp.status or 200), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(f"Нет связи с серверами Microsoft: {exc}") from exc


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _post_form(url: str, payload: dict) -> tuple[int, dict]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    status, raw = _open(url, body, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    return status, _json(raw)


def _post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json", "Accept": "application/json"}
    merged.update(headers or {})
    status, raw = _open(url, body, merged)
    return status, _json(raw)


def _get_json(url: str, headers: dict) -> tuple[int, dict]:
    status, raw = _open(url, None, headers)
    return status, _json(raw)


def _error_text(data: dict) -> str:
    """Понятный текст ошибки вместо сырого ответа Microsoft."""
    description = str(data.get("error_description") or "")
    if "AADSTS" in description:
        code = description.split("AADSTS", 1)[1].split(":", 1)[0].strip()
        return f"Microsoft отклонил вход (AADSTS{code})"
    return str(data.get("error") or "")


def _xui(data: dict) -> dict:
    claims = data.get("DisplayClaims") or {}
    items = claims.get("xui") or []
    return items[0] if items and isinstance(items[0], dict) else {}


# ------------------------------------------------------------- device code

def start_device_code() -> dict:
    """Запрос кода: пользователь вводит его на microsoft.com/link."""
    status, data = _post_form(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if status != 200 or not data.get("user_code"):
        raise AuthError(_error_text(data) or "Microsoft не выдал код входа")
    return {
        "device_code": str(data.get("device_code") or ""),
        "user_code": str(data["user_code"]),
        "verification_uri": str(data.get("verification_uri") or "https://www.microsoft.com/link"),
        "expires_in": int(data.get("expires_in") or 900),
        "interval": int(data.get("interval") or 5),
    }


def _wait(seconds: float, cancel=None) -> bool:
    """Пауза с проверкой отмены; False — пользователь отменил вход."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if cancel is not None and cancel.is_set():
            return False
        time.sleep(min(0.4, max(0.0, end - time.time())))
    return not (cancel is not None and cancel.is_set())


def poll_device_code(device_code: str, interval: int = 5, expires_in: int = 900,
                     cancel=None) -> dict | None:
    """Ожидание подтверждения. None — отменено, AuthError — отказ или истёк код."""
    wait = max(1, int(interval))
    deadline = time.time() + max(30, int(expires_in))
    while time.time() < deadline:
        if not _wait(wait, cancel):
            return None
        status, data = _post_form(TOKEN_URL, {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID,
            "device_code": device_code,
        })
        if status == 200 and data.get("access_token"):
            return data
        error = str(data.get("error") or "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            wait += 5
            continue
        if error == "authorization_declined":
            raise AuthError("Вход отменён в браузере")
        if error == "expired_token":
            raise AuthError("Код входа истёк — нажмите «Войти» заново")
        if error:
            raise AuthError(_error_text(data) or f"Microsoft отказал: {error}")
    raise AuthError("Код входа истёк — нажмите «Войти» заново")


# -------------------------------------------------- Xbox Live → Minecraft

def _xbox_live(ms_access: str) -> tuple[str, str]:
    status, data = _post_json(XBL_URL, {
        "Properties": {"RpsTicket": f"d={ms_access}", "SiteName": "user.auth.xboxlive.com"},
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    })
    token = str(data.get("Token") or "")
    uhs = str(_xui(data).get("uhs") or "")
    if status != 200 or not token or not uhs:
        raise AuthError("Xbox Live не принял вход Microsoft")
    return token, uhs


def _xsts(xbl_token: str) -> tuple[str, str, str]:
    status, data = _post_json(XSTS_URL, {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT",
    })
    if status != 200 or not data.get("Token"):
        xerr = str(data.get("XErr") or "")
        if xerr:
            raise AuthError(XERR.get(xerr, f"Xbox Live отклонил вход (XErr {xerr})"))
        raise AuthError("Xbox Live отклонил вход")
    xui = _xui(data)
    return str(data["Token"]), str(xui.get("uhs") or ""), str(xui.get("xid") or "")


def _minecraft_token(uhs: str, xsts_token: str) -> tuple[str, int]:
    status, data = _post_json(MC_LOGIN_URL, {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"})
    token = str(data.get("access_token") or "")
    if status != 200 or not token:
        raise AuthError("Minecraft Services не выдал токен")
    return token, int(data.get("expires_in") or 86400)


def _profile(mc_token: str) -> tuple[str, str]:
    status, data = _get_json(MC_PROFILE_URL, {"Authorization": f"Bearer {mc_token}"})
    if status == 404:
        raise AuthError("На этом аккаунте Microsoft нет Minecraft: Java Edition")
    if status != 200 or not data.get("id"):
        raise AuthError("Не удалось получить профиль Minecraft")
    return str(data["id"]), str(data.get("name") or "")


def complete_login(tokens: dict) -> dict:
    """Токены Microsoft → аккаунт Minecraft (токен, профиль, xuid)."""
    ms_access = str(tokens.get("access_token") or "")
    if not ms_access:
        raise AuthError("Microsoft не выдал токен")
    xbl_token, _ = _xbox_live(ms_access)
    xsts_token, uhs, xuid = _xsts(xbl_token)
    mc_token, expires_in = _minecraft_token(uhs, xsts_token)
    profile_id, profile_name = _profile(mc_token)
    return {
        "refresh_token": str(tokens.get("refresh_token") or ""),
        "access_token": mc_token,
        "expires_at": int(time.time()) + max(60, expires_in - 60),
        "profile_id": dash_uuid(profile_id),
        "profile_name": profile_name,
        "xuid": xuid,
    }


# -------------------------------------------------------- хранение аккаунта

def dash_uuid(value: str) -> str:
    clean = str(value or "").replace("-", "").lower().strip()
    if len(clean) != 32:
        return str(value or "")
    return f"{clean[0:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:32]}"


def account(cfg: dict) -> dict:
    stored = cfg.get("microsoft_auth")
    return stored if isinstance(stored, dict) else {}


def connected(cfg: dict) -> bool:
    stored = account(cfg)
    return bool(stored.get("access_token") and stored.get("profile_id") and stored.get("profile_name"))


def apply(cfg: dict, data: dict) -> dict:
    """Сохранить аккаунт после успешного входа."""
    previous = account(cfg)
    stored = {
        "refresh_token": str(data.get("refresh_token") or previous.get("refresh_token") or ""),
        "access_token": str(data.get("access_token") or ""),
        "expires_at": int(data.get("expires_at") or 0),
        "profile_id": str(data.get("profile_id") or previous.get("profile_id") or ""),
        "profile_name": str(data.get("profile_name") or previous.get("profile_name") or ""),
        "xuid": str(data.get("xuid") or previous.get("xuid") or ""),
    }
    cfg["microsoft_auth"] = stored
    return stored


def logout(cfg: dict) -> None:
    cfg["microsoft_auth"] = {}
    if str(cfg.get("account_mode") or "") == "microsoft":
        cfg["account_mode"] = "offline"


def refresh_account(cfg: dict) -> dict:
    """Обновить вход по refresh-токену, не спрашивая пароль."""
    stored = account(cfg)
    refresh_token = str(stored.get("refresh_token") or "")
    if not refresh_token:
        raise AuthError("Нет сохранённого входа Microsoft — войдите заново")
    status, data = _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": SCOPE,
    })
    if status != 200 or not data.get("access_token"):
        raise AuthError("Вход Microsoft истёк — войдите заново")
    data.setdefault("refresh_token", refresh_token)
    fresh = complete_login(data)
    if not fresh.get("profile_name"):
        fresh["profile_name"] = str(stored.get("profile_name") or "")
    if not fresh.get("profile_id"):
        fresh["profile_id"] = str(stored.get("profile_id") or "")
    cfg["microsoft_auth"] = fresh
    return fresh


def session_ready(cfg: dict) -> dict:
    """Словарь сессии для launch.build_command."""
    stored = account(cfg)
    return {
        "access_token": str(stored.get("access_token") or ""),
        "profile_id": dash_uuid(str(stored.get("profile_id") or "")),
        "profile_name": str(stored.get("profile_name") or ""),
        "user_type": "msa",
        "xuid": str(stored.get("xuid") or ""),
        "client_id": CLIENT_ID,
    }


def ensure_session(cfg: dict) -> dict:
    """Живая сессия: при необходимости обновляет токен заранее."""
    stored = account(cfg)
    if not connected(cfg):
        raise AuthError("Аккаунт Microsoft не подключён")
    try:
        expires_at = int(stored.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at - REFRESH_MARGIN <= int(time.time()):
        refresh_account(cfg)
    return session_ready(cfg)
