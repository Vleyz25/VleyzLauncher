"""Клиент GigaChat API: OAuth-токен и генерация ответа.

Ключи выдаёт кабинет developers.sber.ru: нужны client_id и client_secret.
Токен живёт около 30 минут, поэтому держим его в памяти до истечения и
обновляем, если сервис ответил 401.

Сертификат GigaChat подписан российским УЦ, которого может не быть в
хранилище Windows. Основной запрос идёт с проверкой TLS; если проверка
сорвалась именно на сертификате, повторяем без неё и пишем предупреждение
в лог, иначе функция не заработает на чистой системе.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .. import APP_VERSION

log = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
SCOPE = "GIGACHAT_API_PERS"
DEFAULT_MODEL = "GigaChat"
DEFAULT_TIMEOUT = 40
TOKEN_MARGIN = 60
DEFAULT_TOKEN_LIFE = 1800


class GigaChatError(RuntimeError):
    """Ошибка GigaChat с текстом, который можно показать пользователю."""


class GigaChat:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.model = str(model or DEFAULT_MODEL)
        self.timeout = timeout
        self._token = ""
        self._expires_at = 0.0

    def _authorization(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _open(self, request: urllib.request.Request):
        return urllib.request.urlopen(
            request, timeout=self.timeout, context=ssl.create_default_context()
        )

    def _post_json(self, url: str, payload: dict | None, headers: dict) -> dict:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with self._open(request) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GigaChatError(_http_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise GigaChatError(f"нет связи с GigaChat: {exc.reason}") from exc
        except ValueError as exc:
            raise GigaChatError("GigaChat вернул не JSON") from exc

    def token(self, force: bool = False) -> str:
        if not force and self._token and time.time() < self._expires_at - TOKEN_MARGIN:
            return self._token
        if not self.client_id or not self.client_secret:
            raise GigaChatError("не заданы client_id и client_secret")
        body = urllib.parse.urlencode({"scope": SCOPE}).encode("utf-8")
        request = urllib.request.Request(
            OAUTH_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": self._authorization(),
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": f"VleyzLauncher/{APP_VERSION}",
            },
        )
        try:
            with self._open(request) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GigaChatError(_http_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise GigaChatError(f"нет связи с GigaChat: {exc.reason}") from exc
        except ValueError as exc:
            raise GigaChatError("GigaChat вернул не JSON при выдаче токена") from exc
        token = str(data.get("access_token") or "")
        if not token:
            raise GigaChatError("GigaChat не выдал токен")
        expires = data.get("expires_at")
        self._expires_at = (
            float(expires) / 1000.0
            if isinstance(expires, (int, float)) and expires > 0
            else time.time() + DEFAULT_TOKEN_LIFE
        )
        self._token = token
        return token

    def ask(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        if not messages:
            raise GigaChatError("пустой запрос к GigaChat")
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        for attempt in (0, 1):
            headers = {
                "Authorization": f"Bearer {self.token(force=bool(attempt))}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"VleyzLauncher/{APP_VERSION}",
            }
            try:
                data = self._post_json(CHAT_URL, payload, headers)
            except GigaChatError as exc:
                # Истёкший токен сервис отдаёт как 401: обновляем и пробуем раз.
                if attempt == 0 and "401" in str(exc):
                    continue
                raise
            return _content(data)
        raise GigaChatError("GigaChat отклонил токен")


def _content(data: dict) -> str:
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GigaChatError("неожиданный ответ GigaChat") from exc
    if not isinstance(text, str) or not text.strip():
        raise GigaChatError("GigaChat вернул пустой ответ")
    return text.strip()


def _http_message(exc: urllib.error.HTTPError) -> str:
    detail = ""
    try:
        body = exc.read().decode("utf-8", "replace")
        parsed = json.loads(body)
        detail = str(parsed.get("message") or parsed.get("error") or "")
    except (ValueError, OSError):
        pass
    detail = detail or str(exc.reason or "")
    return f"GigaChat ответил {exc.code}: {detail}".strip()


def from_config(cfg: dict) -> GigaChat | None:
    """Собрать клиент из настроек. None — ключи не заполнены."""
    block = cfg.get("gigachat")
    if not isinstance(block, dict):
        return None
    client_id = str(block.get("client_id") or "").strip()
    secret = str(block.get("client_secret") or "").strip()
    if not client_id or not secret:
        return None
    return GigaChat(client_id, secret, model=str(block.get("model") or DEFAULT_MODEL))


def enabled(cfg: dict) -> bool:
    block = cfg.get("gigachat")
    return isinstance(block, dict) and bool(block.get("enabled")) and from_config(cfg) is not None
