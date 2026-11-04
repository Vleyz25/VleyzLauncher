from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTH_HOST = "https://authserver.mojang.com"
ACCOUNT_HOST = "https://api.mojang.com"
SESSION_HOST = "https://sessionserver.mojang.com"

# Minecraft 1.16+ спрашивает у api.minecraftservices.com/privileges, можно ли
# играть по сети. Офлайн-токен получает отказ, и кнопка «Сетевая игра» гаснет.
# Локальный ответ говорит клиенту, что права есть; вход на серверы всё равно
# проверяется на их стороне, поэтому лицензию это не заменяет.
PRIVILEGES = {
    "privileges": {
        "onlineChat": {"enabled": True},
        "multiplayerServer": {"enabled": True},
        "multiplayerRealms": {"enabled": True},
    }
}

_server: ThreadingHTTPServer | None = None
_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - имя задаёт http.server
        if self.path.startswith("/privacy/blocklist"):
            payload = {"blockedProfiles": []}
        else:
            payload = PRIVILEGES
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def ensure_server() -> str:
    """Поднять локальный сервер привилегий и вернуть его адрес."""
    global _server
    with _lock:
        if _server is None:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            server.daemon_threads = True
            threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name="vleyz-privileges",
            ).start()
            _server = server
        host, port = _server.server_address[:2]
        return f"http://{host}:{port}"


def jvm_args() -> list[str]:
    """-D-аргументы для клиента: подменяем только сервис привилегий.

    auth/account/session указываем явно: authlib принимает services.host
    только вместе с остальными тремя хостами. Значения совпадают с Mojang,
    поэтому вход в аккаунт Microsoft и проверка сессии не меняются.
    """
    url = ensure_server()
    return [
        f"-Dminecraft.api.auth.host={AUTH_HOST}",
        f"-Dminecraft.api.account.host={ACCOUNT_HOST}",
        f"-Dminecraft.api.session.host={SESSION_HOST}",
        f"-Dminecraft.api.services.host={url}",
    ]
