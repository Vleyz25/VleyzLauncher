"""Аккаунты Ely.by: логин, пароль, 2FA, обновление токена.

Логин + пароль → Yggdrasil-совместимый запрос на authserver.ely.by → accessToken,
который передаётся игре вместе с javaagent authlib-injector (=ely.by). Вариант
авторизации полностью официальный: сервис ely.by сам выдаёт токены, серверы,
поддерживающие Ely.by, проверяют их на своей стороне. Скины и плащи подтягиваются
из системы скинов ely.by автоматом.

Запросы повторяются по документации docs.ely.by («Авторизация для Minecraft»):
POST /auth/authenticate, /auth/refresh, /auth/validate, /auth/invalidate.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from . import net

AUTH_BASE = "https://authserver.ely.by"
RELEASE_API = "https://api.github.com/repos/yushijinhun/authlib-injector/releases/latest"
TIMEOUT = 20


class AuthError(Exception):
    """Ошибка сервера авторизации с понятным текстом для пользователя."""


class TwoFactorError(AuthError):
    """У аккаунта включена двухфакторная аутентификация."""


# ------------------------------------------------------------ HTTP протокол

def _request(url: str, payload: dict | None = None) -> urllib.request.Request:
    headers = {"User-Agent": net.USER_AGENT, "Content-Type": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers)


def _call(path: str, payload: dict | None = None) -> dict:
    """POST на authserver.ely.by; 200 → JSON, иначе HttpError с errorMessage."""
    try:
        with urllib.request.urlopen(_request(AUTH_BASE + path, payload), timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8", errors="replace"))
        except ValueError:
            data = {}
        message = str(data.get("errorMessage") or data.get("error") or exc)
        if "two factor" in message.lower():
            raise TwoFactorError("У аккаунта включён двухфакторный вход (2FA)") from exc
        if exc.code in (400, 401, 403):
            raise AuthError(message) from exc
        if exc.code == 404:
            raise AuthError("Адрес сервера авторизации не отвечает") from exc
        raise AuthError(f"Сервер авторизации ответил {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(f"Нет связи с authserver.ely.by: {exc}") from exc
    if not body:
        return {}
    try:
        result = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise AuthError("Сервер авторизации ответил не по протоколу") from exc
    return result if isinstance(result, dict) else {}


# --------------------------------------------------------------- API методы

def authenticate(login: str, password: str, client_token: str) -> dict:
    """Вход: ответ содержит accessToken и selectedProfile {id, name}."""
    result = _call("/auth/authenticate", {
        "username": login.strip(),
        "password": password,
        "clientToken": client_token,
        "requestUser": True,
    })
    if not result.get("accessToken"):
        raise AuthError("Сервер не выдал токен")
    return result


def refresh(access_token: str, client_token: str) -> dict:
    """Обновление токена без пароля — как предлагает документация Ely.by."""
    return _call("/auth/refresh", {
        "accessToken": access_token,
        "clientToken": client_token,
        "requestUser": True,
    })


def validate(access_token: str) -> bool:
    """Токен живой? Пустой 200 ответ — да, 400/401 — нет/устарел."""
    try:
        _call("/auth/validate", {"accessToken": access_token})
        return True
    except AuthError:
        return False


def invalidate(access_token: str, client_token: str) -> None:
    """Деавторизация токена. Ошибки тихо игнорируются: токен мог уже протухнуть."""
    try:
        _call("/auth/invalidate", {"accessToken": access_token, "clientToken": client_token})
    except AuthError:
        pass


def new_client_token() -> str:
    return secrets.token_hex(16)


def agent_uuid(safe_id: str) -> str:
    """UUID профиля в стандартном формате с дефисами (пусть и строки без них)."""
    clean = str(safe_id).replace("-", "").lower().strip()
    if len(clean) != 32:
        return clean
    return f"{clean[0:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:32]}"


# ---------------------------------------------------------- authlib-injector

def agent_path(data_dir: Path) -> Path:
    return Path(data_dir) / "authlib" / "authlib-injector.jar"


def agent_ready(data_dir: Path, sha256: str | None = None) -> bool:
    """Jar скачан и валиден по размеру/методу zip, при наличии — и по SHA256."""
    path = agent_path(data_dir)
    if not path.is_file() or path.stat().st_size < 200_000:
        return False
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"PK":
                return False
    except OSError:
        return False
    if sha256 and net.sha256_file(path) != sha256:
        return False
    return True


def download_agent(data_dir: Path, log=None, cancel=None) -> Path:
    """Скачивает authlib-injector с GitHub (официальные релизы) с проверкой SHA256."""
    data_dir = Path(data_dir)
    if callable(log):
        log("Скачиваю authlib-injector…")
    release = net.fetch_json(RELEASE_API)
    asset = next((item for item in release.get("assets", []) if str(item.get("name", "")).endswith(".jar")), None)
    if not asset:
        raise AuthError("Не удалось найти релиз authlib-injector")
    url = str(asset.get("browser_download_url") or "")
    expected = ""
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        expected = digest.split(":", 1)[1]
    elif isinstance(asset.get("size"), int):
        expected = ""  # проверим размером, если хэш не дал GitHub API
    dest = agent_path(data_dir)
    net.download(
        url, dest,
        sha1=None,
        size=(asset.get("size") or None) if not expected else None,
        on_bytes=None,
        cancel=cancel,
        timeout=120,
    )
    if expected and net.sha256_file(dest) != expected:
        dest.unlink(missing_ok=True)
        raise AuthError("Скачанный authlib-injector не прошёл проверку целостности")
    if callable(log):
        log("authlib-injector готов")
    return dest


# -------------------------------------------------------- хранение аккаунта

def account(cfg: dict) -> dict:
    """Текущий ely-аккаунт из конфига (может быть пустым)."""
    stored = cfg.get("ely_auth")
    return stored if isinstance(stored, dict) else {}


def connected(cfg: dict) -> bool:
    """Аккаунт подключён: есть токен и профиль от сервера."""
    stored = account(cfg)
    return bool(stored.get("access_token") and stored.get("profile_id") and stored.get("profile_name"))


def apply_auth(cfg: dict, auth: dict, login: str, password: str) -> dict:
    """Сохранить аккаунт в конфиге после успешного входа/обновления.

    Пароль хранится, как у TLauncher, чтобы уметь перелогиниться, когда токен
    окончательно истёк. Локальный конфиг — данные пользователя на его ПК.
    """
    profile = auth.get("selectedProfile") or (auth.get("availableProfiles") or [{}])[0] or {}
    stored = account(cfg)
    cfg["ely_auth"] = {
        "login": (login or "").strip(),
        "password": password,
        "client_token": str(auth.get("clientToken") or stored.get("client_token") or new_client_token()),
        "access_token": str(auth.get("accessToken") or ""),
        "profile_id": str(profile.get("id") or stored.get("profile_id") or ""),
        "profile_name": str(profile.get("name") or stored.get("profile_name") or ""),
    }
    cfg["ely_enabled"] = True
    return cfg["ely_auth"]


def logout(cfg: dict) -> None:
    stored = account(cfg)
    if stored.get("access_token"):
        invalidate(str(stored.get("access_token")), str(stored.get("client_token") or ""))
    cfg["ely_auth"] = {}
    cfg["ely_enabled"] = False


def session_ready(cfg: dict) -> dict:
    """Готовый словарь сессии для build_command (account уже с живым токеном)."""
    stored = account(cfg)
    return {
        "access_token": str(stored.get("access_token") or ""),
        "profile_id": agent_uuid(stored.get("profile_id")),
        "profile_name": str(stored.get("profile_name") or ""),
        "user_type": "mojang",
    }


def launch_session(cfg: dict, data_dir: Path, log=None, cancel=None) -> dict:
    """Сессия для запуска: продлевает токен и готовит authlib-injector.

    Если accessToken протух — сначала пробуем /auth/refresh, затем вход
    по сохранённым логину и паролю. Без живого токена запускать игру нельзя.
    """
    stored = account(cfg)
    if not connected(cfg):
        raise AuthError("Аккаунт Ely.by не подключён")
    token = str(stored.get("access_token") or "")
    if not validate(token):
        renewed = None
        try:
            renewed = refresh(token, str(stored.get("client_token") or new_client_token()))
        except AuthError:
            renewed = None
        if not renewed or not renewed.get("accessToken"):
            login = str(stored.get("login") or "")
            password = str(stored.get("password") or "")
            if not login or not password:
                raise AuthError("Сессия Ely.by истекла — войдите заново")
            renewed = authenticate(login, password, str(stored.get("client_token") or new_client_token()))
        apply_auth(cfg, renewed, str(stored.get("login") or ""), str(stored.get("password") or ""))
    if not agent_ready(data_dir):
        download_agent(data_dir, log=log, cancel=cancel)
    session = session_ready(cfg)
    session["agent_jar"] = str(agent_path(data_dir))
    return session
