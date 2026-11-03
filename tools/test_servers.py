"""Проверка сетевого ядра: пинг-протокол на мок-сервере, NBT servers.dat, аргументы запуска."""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vleyzlauncher.core import servers as core  # noqa: E402


def pack_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def mock_server(port: int, status: dict) -> None:
    """Минимальный SLP-сервер: принимает рукопожатие и отвечает статусом."""
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        conn, _ = sock.accept()
        with conn:
            conn.recv(4096)  # рукопожатие + status-запрос
            payload = json.dumps(status).encode("utf-8")
            packet = pack_varint(0) + pack_varint(len(payload)) + payload
            conn.sendall(pack_varint(len(packet)) + packet)
            conn.recv(64)


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(("OK  " if condition else "FAIL") + " " + name)
        if not condition:
            failures.append(name)

    # --- адреса
    check("parse host", core.parse_address("mc.example.com") == ("mc.example.com", 25565))
    check("parse host:port", core.parse_address("play.net:25577") == ("play.net", 25577))
    check("parse bad port", core.parse_address("play.net:0") is None)
    check("parse ipv6", core.parse_address("[::1]:25566") == ("::1", 25566))
    check("format default", core.format_address("mc.example.com", 25565) == "mc.example.com")
    check("format custom", core.format_address("mc.example.com", 25577) == "mc.example.com:25577")

    # --- пинг протокола на мок-сервере
    status = {
        "version": {"name": "1.16.5"},
        "players": {"online": 3, "max": 20},
        "description": {"text": "§aПривет§r из мок-сервера"},
    }
    server_thread = threading.Thread(target=mock_server, args=(25599, status), daemon=True)
    server_thread.start()
    result = core.ping("127.0.0.1:25599", timeout=5)
    check("ping ok", result.get("ok") is True)
    check("ping motd без §-кодов", result.get("motd") == "Привет из мок-сервера")
    check("ping online", (result.get("online"), result.get("max")) == (3, 20))
    check("ping version", result.get("version") == "1.16.5")
    check("ping address", result.get("address") == "127.0.0.1:25599")

    dead = core.ping("127.0.0.1:47000", timeout=1)
    check("ping закрытого порта", dead.get("ok") is False and dead.get("error"))

    # --- servers.dat: запись и чтение туда-обратно
    tmp = Path(__file__).resolve().parent / "_tmp_servers_dat"
    tmp.mkdir(exist_ok=True)
    path = tmp / "servers.dat"
    entries = [
        {"name": "Мой сервер", "ip": "mc.example.com"},
        {"name": "Локальный", "ip": "192.168.1.5:25565"},
    ]
    core._write_servers_dat(path, entries)
    readback = core._read_servers_dat(path)
    check("nbt roundtrip", readback == entries)
    raw = path.read_bytes()
    check("nbt gzip", raw[:2] == b"\x1f\x8b")
    # корень: 0A 00 00, затем TAG_List
    import gzip as gzip_mod
    data = gzip_mod.decompress(raw)
    check("nbt структура", data[:3] == b"\x0a\x00\x00" and data[3] == 0x09)

    # --- конфиг
    cfg: dict = {}
    created = core.create(cfg, "Тест", "mc.example.com:25577")
    check("create server", bool(created) and len(core.servers(cfg)) == 1)
    check("create bad address", core.create(cfg, "плохой", "") is None)
    check("update server", core.update(cfg, created["id"], name="Новое имя") is True)
    check("remove server", core.remove(cfg, created["id"]) is True and not core.servers(cfg))

    # --- аргументы быстрого входа
    check("quickplay 1.16.5", core.quick_play_args("1.16.5-forge-36.2.34", "mc.example.com", 25565)
          == ["--server", "mc.example.com", "--port", "25565"])
    check("quickplay 1.20.1", core.quick_play_args("1.20.1-forge-47.4.28", "mc.example.com", 25577)
          == ["--quickPlayMultiplayer", "mc.example.com:25577"])

    for leftover in tmp.iterdir():
        leftover.unlink()
    tmp.rmdir()

    print()
    if failures:
        print("ПРОВАЛЕНО:", ", ".join(failures))
        return 1
    print("Всё прошло.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
