"""Сетевая игра: список серверов, пинг (Server List Ping), быстрый вход.

Пинг говорит с сервером тем же протоколом, что и экран «Сетевая игра» в Minecraft:
TCP-рукопожатие → status-запрос → JSON с MOTD, онлайном и версией. Адреса без
порта сначала проверяются по SRV-записи `_minecraft._tcp.<хост>` — как делает
сама игра, поэтому `mc.example.com` без порта работает правильно.
"""

from __future__ import annotations

import gzip
import json
import re
import socket
import struct
import subprocess
import time
import uuid
from pathlib import Path

DEFAULT_PORT = 25565
CREATE_NO_WINDOW = 0x08000000
MAX_STATUS_BYTES = 4 * 1024 * 1024

IP_LIKE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


# ------------------------------------------------------------- список серверов

def servers(cfg: dict) -> list[dict]:
    items = cfg.get("servers")
    if not isinstance(items, list):
        items = []
        cfg["servers"] = items
    return items


def parse_address(value: str) -> tuple[str, int] | None:
    """`host`, `host:port`, `[ipv6]`, `[ipv6]:port` → (host, port); None если пусто."""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("["):
        match = re.match(r"^\[([0-9a-fA-F:]+)\](?::(\d+))?$", value)
        if not match:
            return None
        port = int(match.group(2)) if match.group(2) else DEFAULT_PORT
        return match.group(1), port
    if value.count(":") == 1:
        host, _, port_text = value.rpartition(":")
        if not host or not port_text.isdigit() or not 0 < int(port_text) < 65536:
            return None
        return host, int(port_text)
    if ":" in value:  # голый IPv6
        return value, DEFAULT_PORT
    return value, DEFAULT_PORT


def format_address(host: str, port: int) -> str:
    if ":" in host:
        return f"[{host}]:{port}" if port != DEFAULT_PORT else f"[{host}]"
    return f"{host}:{port}" if port != DEFAULT_PORT else host


def create(cfg: dict, name: str, address: str) -> dict | None:
    if not parse_address(address):
        return None
    server = {
        "id": uuid.uuid4().hex[:8],
        "name": (name or "").strip()[:28] or (address or "").strip()[:28],
        "address": (address or "").strip(),
    }
    servers(cfg).append(server)
    return server


def update(cfg: dict, server_id: str, name: str | None = None, address: str | None = None) -> bool:
    for server in servers(cfg):
        if server.get("id") != server_id:
            continue
        if name is not None:
            server["name"] = name.strip()[:28] or server.get("name", "Сервер")
        if address is not None and parse_address(address):
            server["address"] = address.strip()
        return True
    return False


def remove(cfg: dict, server_id: str) -> bool:
    items = servers(cfg)
    remaining = [item for item in items if item.get("id") != server_id]
    if len(remaining) == len(items):
        return False
    cfg["servers"] = remaining
    return True


# ------------------------------------------------------------------- пинг

def _pack_varint(value: int) -> bytes:
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


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("сервер закрыл соединение")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_varint(sock: socket.socket) -> int:
    number = 0
    for index in range(5):
        byte = _recv_exact(sock, 1)[0]
        number |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return number
    raise ValueError("некорректный ответ сервера")


def _srv_lookup(host: str) -> tuple[str, int] | None:
    """SRV-запись `_minecraft._tcp.<хост>` через nslookup (для адресов без порта)."""
    if IP_LIKE.match(host) or ":" in host or not re.match(r"^[A-Za-z0-9._-]+$", host):
        return None
    try:
        result = subprocess.run(
            ["nslookup", "-type=srv", f"_minecraft._tcp.{host}"],
            capture_output=True, text=True, errors="replace", timeout=3,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    port_match = re.search(r"port\s*=\s*(\d+)", result.stdout)
    host_match = re.search(r"svr hostname\s*=\s*(\S+)", result.stdout)
    if port_match and host_match:
        return host_match.group(1).rstrip("."), int(port_match.group(1))
    return None


def resolve_address(address: str) -> tuple[str, int] | None:
    """Адрес → (host, port) с учётом SRV-записи: так подключается сама игра."""
    parsed = parse_address(address)
    if not parsed:
        return None
    host, port = parsed
    if port == DEFAULT_PORT:
        srv = _srv_lookup(host)
        if srv:
            return srv
    return host, port


def _plain_motd(description) -> str:
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        parts = [str(description.get("text") or "")]
        for item in description.get("extra") or []:
            parts.append(_plain_motd(item))
        return "".join(parts)
    if isinstance(description, list):
        return "".join(_plain_motd(item) for item in description)
    return ""


def ping(address: str, timeout: float = 4.0) -> dict:
    """SLP-пинг сервера. Возвращает словарь с ok, motd, online, max, version, latency_ms."""
    resolved = parse_address(address)
    if not resolved:
        return {"ok": False, "error": "некорректный адрес"}
    host, port = resolved
    if port == DEFAULT_PORT:
        srv = _srv_lookup(host)
        if srv:
            host, port = srv
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            host_bytes = host.encode("utf-8")
            handshake = (
                _pack_varint(0x00)
                + _pack_varint(-1)
                + struct.pack(">H", len(host_bytes)) + host_bytes
                + struct.pack(">H", port)
                + _pack_varint(1)
            )
            sock.sendall(_pack_varint(len(handshake)) + handshake)
            request = _pack_varint(0x00)
            sock.sendall(_pack_varint(len(request)) + request)

            total = _read_varint(sock)
            if total <= 0 or total > MAX_STATUS_BYTES:
                raise ValueError("некорректный ответ сервера")
            if _read_varint(sock) != 0x00:
                raise ValueError("некорректный ответ сервера")
            length = _read_varint(sock)
            if length > MAX_STATUS_BYTES:
                raise ValueError("некорректный ответ сервера")
            payload = _recv_exact(sock, length)
        latency = int((time.perf_counter() - started) * 1000)
    except (OSError, ValueError, ConnectionError) as exc:
        message = "не отвечает" if isinstance(exc, (socket.timeout, TimeoutError)) else str(exc)
        return {"ok": False, "error": message or "не отвечает"}

    try:
        status = json.loads(payload.decode("utf-8", errors="replace"))
    except ValueError:
        return {"ok": False, "error": "сервер ответил не по протоколу"}
    if not isinstance(status, dict):
        return {"ok": False, "error": "сервер ответил не по протоколу"}

    players = status.get("players") or {}
    version = status.get("version") or {}
    motd = re.sub(r"§.", "", _plain_motd(status.get("description"))).strip()
    return {
        "ok": True,
        "motd": motd[:90],
        "online": int(players.get("online") or 0),
        "max": int(players.get("max") or 0),
        "version": str(version.get("name") or "")[:40],
        "latency_ms": latency,
        "address": format_address(host, port),
    }


def local_ip() -> str:
    """Локальный IP для игры по LAN (без отправки трафика)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0)
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "—"


# ------------------------------------------------------------- быстрый вход

def _version_tuple(version_id: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(version_id))
    if not match:
        return (0,)
    parts = [int(match.group(1)), int(match.group(2))]
    if match.group(3):
        parts.append(int(match.group(3)))
    return tuple(parts)


def quick_play_args(version_id: str, host: str, port: int) -> list[str]:
    """Аргументы автоподключения: с 1.20 — quickPlay, раньше --server/--port."""
    if _version_tuple(version_id) >= (1, 20):
        return ["--quickPlayMultiplayer", f"{host}:{port}"]
    return ["--server", str(host), "--port", str(port)]


# ------------------------------------------------------------- servers.dat

def _nbt_payload(tag: int, value) -> bytes:
    if tag == 1:
        return struct.pack(">b", value)
    if tag == 2:
        return struct.pack(">h", value)
    if tag == 3:
        return struct.pack(">i", value)
    if tag == 4:
        return struct.pack(">q", value)
    if tag == 5:
        return struct.pack(">f", value)
    if tag == 6:
        return struct.pack(">d", value)
    if tag == 7:
        return struct.pack(">i", len(value)) + bytes(value)
    if tag == 8:
        raw = str(value).encode("utf-8")
        return struct.pack(">H", len(raw)) + raw
    raise ValueError(f"неподдерживаемый тег {tag}")


_NBT_SIZES = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}


def _nbt_read_payload(data: bytes, pos: int, tag: int) -> tuple[object, int]:
    def take(size: int) -> tuple[bytes, int]:
        end = pos + size
        return data[pos:end], end

    if tag == 0:
        return None, pos
    if tag in _NBT_SIZES:
        raw, pos = take(_NBT_SIZES[tag])
        fmt = {1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}[tag]
        return struct.unpack(fmt, raw)[0], pos
    if tag == 7:
        raw, pos = take(4)
        length = struct.unpack(">i", raw)[0]
        raw, pos = take(max(0, length))
        return raw, pos
    if tag == 8:
        raw, pos = take(2)
        length = struct.unpack(">H", raw)[0]
        raw, pos = take(length)
        return raw.decode("utf-8", errors="replace"), pos
    if tag == 9:
        raw, pos = take(5)
        item_tag = raw[0]
        length = struct.unpack(">i", raw[1:])[0]
        items: list = []
        for _ in range(max(0, length)):
            value, pos = _nbt_read_payload(data, pos, item_tag)
            items.append(value)
        return items, pos
    if tag == 10:
        result: dict = {}
        while True:
            raw, pos = take(1)
            inner_tag = raw[0]
            if inner_tag == 0:
                return result, pos
            raw, pos = take(2)
            name_length = struct.unpack(">H", raw)[0]
            raw, pos = take(name_length)
            name = raw.decode("utf-8", errors="replace")
            value, pos = _nbt_read_payload(data, pos, inner_tag)
            result[name] = value
        return result, pos  # pragma: no cover
    if tag in (11, 12):
        width = 4 if tag == 11 else 8
        raw, pos = take(4)
        length = struct.unpack(">i", raw)[0]
        _, pos = take(max(0, length) * width)
        return None, pos
    raise ValueError(f"неизвестный тег {tag}")


def _read_servers_dat(path: Path) -> list[dict]:
    """Читает servers.dat; теги-числа не нужны, важны name/ip/icon каждого сервера."""
    raw = path.read_bytes()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    root_tag = data[0]
    if root_tag != 10:
        return []
    pos = 3  # тег корня (1 байт) + длина пустого имени корня (2 байта)
    value, _ = _nbt_read_payload(data, pos, root_tag)
    entries = value.get("servers") if isinstance(value, dict) else None
    result: list[dict] = []
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if isinstance(entry, dict) and entry.get("ip"):
            item = {"name": str(entry.get("name") or entry.get("ip")), "ip": str(entry.get("ip"))}
            if isinstance(entry.get("icon"), str):
                item["icon"] = entry["icon"]
            result.append(item)
    return result


def _write_nbt_string_named(name: str, value: str) -> bytes:
    raw_name = name.encode("utf-8")
    payload = _nbt_payload(8, value)
    return b"\x08" + struct.pack(">H", len(raw_name)) + raw_name + payload


def _write_servers_dat(path: Path, entries: list[dict]) -> None:
    items = bytearray()
    for entry in entries:
        items += _write_nbt_string_named("name", str(entry.get("name") or "Сервер"))
        items += _write_nbt_string_named("ip", str(entry.get("ip")))
        icon = entry.get("icon")
        if icon:
            items += _write_nbt_string_named("icon", str(icon))
        items += b"\x00"  # конец составного тега — элемента списка
    payload = (
        b"\x0a\x00\x00"                                   # корневой TAG_Compound с пустым именем
        + b"\x09" + struct.pack(">H", 7) + b"servers"     # TAG_List «servers»
        + b"\x0a" + struct.pack(">i", len(entries))       # тип элемента — Compound и количество
        + bytes(items)
        + b"\x00"                                         # конец корневого Compound
    )
    path.write_bytes(gzip.compress(payload))


def sync_servers_dat(data_dir: Path, cfg: dict) -> None:
    """Обновляет servers.dat в папке игры: серверы лаунчера + добавленные в игре.

    Экран «Сетевая игра» показывает тот же список, что и лаунчер; записи,
    добавленные руками в игре и не совпадающие по IP, сохраняются.
    """
    launcher_entries = [
        {"name": item.get("name") or item.get("address"), "ip": item.get("address") or ""}
        for item in servers(cfg)
    ]
    path = Path(data_dir) / "servers.dat"
    existing: list[dict] = []
    if path.is_file():
        try:
            existing = _read_servers_dat(path)
        except (OSError, ValueError, struct.error):
            existing = []
    known_ips = {entry["ip"] for entry in launcher_entries}
    kept = [entry for entry in existing if entry.get("ip") not in known_ips]
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        _write_servers_dat(path, launcher_entries + kept)
    except OSError:
        pass
