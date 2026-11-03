from __future__ import annotations

import hashlib
import os
import subprocess
import time
import uuid
from pathlib import Path

from . import versions

CREATE_NO_WINDOW = 0x08000000


class LaunchError(Exception):
    pass


def offline_uuid(name: str) -> str:
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    raw = bytearray(digest)
    raw[6] = (raw[6] & 0x0F) | 0x30
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _substitute(value: str, mapping: dict[str, str]) -> str:
    for key, replacement in mapping.items():
        value = value.replace("${" + key + "}", replacement)
    return value


def _value_list(entry: dict) -> list[str]:
    """В arguments.value Mojang иногда кладёт строку вместо списка."""
    value = entry.get("value")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _jvm_arguments(vjson: dict) -> list[str]:
    arguments = vjson.get("arguments") or {}
    result: list[str] = []
    for arg in arguments.get("jvm", []) or []:
        if isinstance(arg, str):
            result.append(arg)
        elif isinstance(arg, dict) and versions.rules_allow(arg.get("rules")):
            result.extend(_value_list(arg))
    return result


def _game_arguments(vjson: dict) -> list[str]:
    legacy = vjson.get("minecraftArguments")
    if legacy:
        return legacy.split()
    arguments = vjson.get("arguments") or {}
    result: list[str] = []
    for arg in arguments.get("game", []) or []:
        if isinstance(arg, str):
            result.append(arg)
        elif isinstance(arg, dict) and versions.rules_allow(arg.get("rules")):
            result.extend(_value_list(arg))
    return result


def natives_dir(data_dir: Path, vjson: dict, version_id: str) -> Path:
    """Нативы всегда лежат в папке той версии, чей client.jar используется."""
    base = Path(data_dir) / "versions"
    jar_id = versions.client_jar_id(vjson, version_id)
    candidate = base / jar_id / "natives"
    if candidate.is_dir():
        return candidate
    return base / version_id / "natives"


LOADER_PREFIXES = ("net.minecraftforge:forge:", "net.neoforged:forge:", "net.neoforged:neoforge:")


def loader_extra_jars(vjson: dict, libraries_dir: Path) -> list[Path]:
    """Forge/NeoForge кладут launch target в отдельный *-client.jar, которого нет в json.

    Без него ModLauncher падает с «Cannot find launch target fmlclient».
    """
    result: list[Path] = []
    libraries_dir = Path(libraries_dir)
    for lib in vjson.get("libraries", []):
        name = str(lib.get("name") or "")
        if not name.startswith(LOADER_PREFIXES):
            continue
        relative = versions.library_path(lib)
        if not relative:
            continue
        artifact = libraries_dir / relative
        for suffix in ("-client.jar", "-universal.jar"):
            candidate = artifact.with_name(artifact.name.replace(".jar", suffix))
            if candidate.is_file() and candidate != artifact and candidate not in result:
                result.append(candidate)
    return result


def build_command(
    data_dir: Path,
    version_id: str,
    nickname: str,
    ram_mb: int,
    java: Path,
    server: tuple[str, int] | None = None,
    session: dict | None = None,
) -> list[str]:
    """Команда запуска; server=(host, port) сразу подключает игру к серверу.

    session — словарь аккаунта Ely.by {access_token, profile_id, profile_name,
    user_type}: тогда ник/uuid/токен берутся от ely.by и добавляется
    javaagent authlib-injector (=ely.by). Без session — офлайн-режим по нику.
    """
    data_dir = Path(data_dir)
    vjson = versions.resolved_json(data_dir, version_id)
    version_dir = data_dir / "versions" / version_id
    jar_id = versions.client_jar_id(vjson, version_id)
    client_jar = data_dir / "versions" / jar_id / f"{jar_id}.jar"
    libraries = versions.library_classpath_entries(vjson, data_dir / "libraries")
    classpath_parts = [str(path) for path in libraries]
    jvm_args = _jvm_arguments(vjson)
    module_mode = any(
        arg in ("-p", "--module-path") or arg.startswith("--module-path=") for arg in jvm_args
    )
    if not module_mode:
        for extra in loader_extra_jars(vjson, data_dir / "libraries"):
            if str(extra) not in classpath_parts:
                classpath_parts.append(str(extra))
    if client_jar not in libraries:
        classpath_parts.append(str(client_jar))

    natives = natives_dir(data_dir, vjson, version_id)
    asset_index = (vjson.get("assetIndex") or {}).get("id") or vjson.get("assets") or version_id

    mapping = {
        "auth_player_name": str(session.get("profile_name") or nickname) if session else nickname,
        "version_name": version_id,
        "game_directory": str(data_dir),
        "assets_root": str(data_dir / "assets"),
        "game_assets": str(data_dir / "assets" / "virtual" / "legacy"),
        "assets_index_name": asset_index,
        "auth_uuid": str(session.get("profile_id") or offline_uuid(nickname)) if session else offline_uuid(nickname),
        "auth_access_token": str(session.get("access_token") or "0") if session else "0",
        "auth_session": str(session.get("access_token") or "0") if session else "0",
        "clientid": str(session.get("client_id") or "") if session else "",
        "auth_xuid": str(session.get("xuid") or "") if session else "",
        "user_type": str(session.get("user_type") or "legacy") if session else "legacy",
        "user_properties": "{}",
        "version_type": vjson.get("type", "release"),
        "natives_directory": str(natives),
        "launcher_name": "VleyzLauncher",
        "launcher_version": "1.1",
        "classpath": os.pathsep.join(classpath_parts),
        "classpath_separator": os.pathsep,
        "library_directory": str(data_dir / "libraries"),
        "resolution_width": "925",
        "resolution_height": "530",
    }

    command = [str(java), f"-Xmx{int(ram_mb)}M", f"-Xms{min(1024, int(ram_mb))}M"]
    agent_jar = str(session.get("agent_jar") or "") if session else ""
    if agent_jar:
        command.append(f"-javaagent:{agent_jar}=ely.by")

    logging_cfg = ((vjson.get("logging") or {}).get("client") or {})
    log_argument = logging_cfg.get("argument")
    log_file = logging_cfg.get("file") or {}
    if log_argument and log_file.get("id"):
        log_path = data_dir / "assets" / "log_configs" / str(log_file["id"])
        if log_path.is_file():
            command.append(_substitute(str(log_argument), {"path": str(log_path)}))

    jvm_args = _jvm_arguments(vjson)
    if jvm_args:
        command.extend(_substitute(arg, mapping) for arg in jvm_args)
    else:
        command.extend([f"-Djava.library.path={natives}", "-cp", mapping["classpath"]])
    command.append(str(vjson.get("mainClass") or "net.minecraft.client.main.Main"))
    command.extend(_substitute(arg, mapping) for arg in _game_arguments(vjson))
    if server is not None:
        from . import servers as servers_core

        command.extend(servers_core.quick_play_args(version_id, server[0], server[1]))
    return command


def launch(
    data_dir: Path,
    version_id: str,
    nickname: str,
    ram_mb: int,
    java: Path,
    server: tuple[str, int] | None = None,
    session: dict | None = None,
) -> tuple[subprocess.Popen, Path]:
    command = build_command(data_dir, version_id, nickname, ram_mb, java, server, session)
    logs_dir = Path(data_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"vleyz-{time.strftime('%Y%m%d-%H%M%S')}.log"
    handle = open(log_path, "w", encoding="utf-8", errors="replace")
    handle.write(" ".join(command) + "\n\n")
    handle.flush()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(data_dir),
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    except (OSError, ValueError) as exc:
        handle.close()
        raise LaunchError(f"Java не смогла запустить игру: {exc}") from exc
    return process, log_path


def read_log_tail(log_path: Path, lines: int = 12) -> list[str]:
    try:
        content = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in content.strip().splitlines() if line.strip()][-lines:]


def latest_crash_report(data_dir: Path, since: float) -> Path | None:
    """Свежий crash-report, появившийся после запуска игры."""
    folder = Path(data_dir) / "crash-reports"
    if not folder.is_dir():
        return None
    newest: Path | None = None
    for path in folder.glob("crash-*.txt"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if modified < since - 5:
            continue
        if newest is None or modified > newest.stat().st_mtime:
            newest = path
    return newest


def crash_summary(report: Path) -> str:
    """Короткая причина падения: описание и первая строка исключения."""
    try:
        lines = Path(report).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    description = ""
    error = ""
    for line in lines[:40]:
        stripped = line.strip()
        if not description and stripped.startswith("Description:"):
            description = stripped.split(":", 1)[1].strip()
        if not error and ("Exception" in stripped or "Error" in stripped) and ":" in stripped:
            error = stripped.split(" at ")[0][:120]
        if description and error:
            break
    parts = [part for part in (description, error) if part]
    return " · ".join(parts)[:150]


def is_installed(data_dir: Path, version_id: str) -> bool:
    version_dir = Path(data_dir) / "versions" / version_id
    return (version_dir / ".vleyz-installed").is_file() and (version_dir / f"{version_id}.jar").is_file()
