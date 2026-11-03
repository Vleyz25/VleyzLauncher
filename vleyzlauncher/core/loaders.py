from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import net, versions

FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
FORGE_METADATA = f"{FORGE_MAVEN}/maven-metadata.xml"
NEO_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
NEO_METADATA = f"{NEO_MAVEN}/maven-metadata.xml"

TTL = 60 * 60
_cache: dict[str, tuple[float, object]] = {}


class LoaderError(Exception):
    """Загрузчик недоступен для этой версии игры или сервис не ответил."""


LOADERS = [
    {
        "id": "vanilla",
        "label": "Vanilla",
        "subtitle": "Чистая игра",
        "desc": "Без модов и загрузчиков. То, что отдаёт Mojang.",
        "accent": "#4ADE80",
    },
    {
        "id": "fabric",
        "label": "Fabric",
        "subtitle": "Лёгкий загрузчик модов",
        "desc": "Быстрый старт, минимум изменений. Основа для Sodium, Iris и большинства",
        "accent": "#E9D5A1",
    },
    {
        "id": "forge",
        "label": "Forge",
        "subtitle": "Классика моддинга",
        "desc": "Самый большой каталог модов и сборок, начиная с 1.7.10.",
        "accent": "#E07B39",
    },
    {
        "id": "neoforge",
        "label": "NeoForge",
        "subtitle": "Форк Forge",
        "desc": "Современная ветка Forge для 1.20.2 и новее.",
        "accent": "#C084FC",
    },
]

LOADER_BY_ID = {item["id"]: item for item in LOADERS}


def clear_cache() -> None:
    _cache.clear()


def _cached(key: str, factory):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < TTL:
        return hit[1]
    value = factory()
    _cache[key] = (now, value)
    return value


def _numbers(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text))


# ---------------------------------------------------------------- fabric

def fabric_loader_version(game: str) -> str:
    def factory():
        data = net.fetch_json(f"{FABRIC_META}/{game}")
        if not isinstance(data, list) or not data:
            raise LoaderError(f"Fabric не поддерживает {game}")
        return data

    data = _cached(f"fabric:{game}", factory)
    stable = [item for item in data if (item.get("loader") or {}).get("stable")]
    pick = (stable or data)[0]
    version = (pick.get("loader") or {}).get("version")
    if not version:
        raise LoaderError(f"Fabric не поддерживает {game}")
    return str(version)


def fabric_profile_url(game: str, loader_version: str) -> str:
    return f"{FABRIC_META}/{game}/{loader_version}/profile/json"


# ----------------------------------------------------------------- forge

def _forge_from_metadata(game: str) -> str | None:
    def factory():
        return net.fetch_text(FORGE_METADATA)

    try:
        text = _cached("forge:metadata", factory)
    except net.DownloadError:
        return None
    found = [item for item in re.findall(r"<version>([^<]+)</version>", text) if f"-{game}-" not in item]
    found = [item for item in found if item.startswith(f"{game}-")]
    if not found:
        return None
    return max(found, key=_numbers).split("-", 1)[1]


def forge_version(game: str) -> str:
    def factory():
        return net.fetch_json(FORGE_PROMOS)

    try:
        promos = (_cached("forge:promos", factory) or {}).get("promos", {})
    except net.DownloadError:
        promos = {}
    for key in (f"{game}-recommended", f"{game}-latest"):
        if promos.get(key):
            return str(promos[key])
    fallback = _forge_from_metadata(game)
    if fallback:
        return fallback
    raise LoaderError(f"Forge не выпускался для {game}")


def forge_installer_url(game: str, forge_id: str) -> str:
    return f"{FORGE_MAVEN}/{game}-{forge_id}/forge-{game}-{forge_id}-installer.jar"


# -------------------------------------------------------------- neoforge

def neoforge_version(game: str) -> str:
    def factory():
        return net.fetch_text(NEO_METADATA)

    try:
        text = _cached("neo:metadata", factory)
    except net.DownloadError as exc:
        raise LoaderError("Сервис NeoForge не ответил") from exc

    parts = (game or "").split(".")
    if len(parts) < 2 or parts[0] != "1":
        raise LoaderError(f"NeoForge не поддерживает {game}")
    minor = int(parts[1]) if parts[1].isdigit() else 0
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    prefix = f"{minor}.{patch}."
    found = [item for item in re.findall(r"<version>([^<]+)</version>", text) if item.startswith(prefix)]
    if not found:
        raise LoaderError(f"NeoForge нет для {game} (нужна 1.20.2 или новее)")
    return max(found, key=_numbers)


def neoforge_installer_url(neo_id: str) -> str:
    return f"{NEO_MAVEN}/{neo_id}/neoforge-{neo_id}-installer.jar"


# ------------------------------------------------------------------ план

def _game_parts(game: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", game))


def supports(loader: str, game: str) -> bool:
    """Быстрая проверка без сети: заведомо невозможные сочетания отсекаем сразу."""
    if loader in ("vanilla", "fabric", "forge"):
        return True
    if loader == "neoforge":
        return _game_parts(game) >= (1, 20, 2)
    return False


def plan(data_dir: Path, game: str, loader: str = "vanilla") -> dict:
    """Что и откуда качать для выбранной версии и загрузчика."""
    loader = loader or "vanilla"
    if loader not in LOADER_BY_ID:
        raise LoaderError(f"Неизвестный загрузчик: {loader}")
    if not supports(loader, game):
        raise LoaderError(f"{LOADER_BY_ID[loader]['label']} не поддерживает {game}")

    base = {
        "game": game,
        "loader": loader,
        "kind": "vanilla" if loader == "vanilla" else "profile",
        "version_id": game,
        "loader_version": "",
        "profile_url": "",
        "installer_url": "",
        "label": "Vanilla",
        "accent": LOADER_BY_ID[loader]["accent"],
    }

    if loader == "vanilla":
        return base

    if loader == "fabric":
        version = fabric_loader_version(game)
        base.update(
            kind="profile",
            version_id=f"{game}-fabric",
            loader_version=version,
            profile_url=fabric_profile_url(game, version),
            label=f"Fabric {version}",
        )
        return base

    if loader == "forge":
        version = forge_version(game)
        base.update(
            kind="installer",
            version_id=f"{game}-forge-{version}",
            loader_version=version,
            installer_url=forge_installer_url(game, version),
            label=f"Forge {version}",
        )
        return base

    version = neoforge_version(game)
    base.update(
        kind="installer",
        version_id=f"neoforge-{version}",
        loader_version=version,
        installer_url=neoforge_installer_url(version),
        label=f"NeoForge {version}",
    )
    return base


def preview(game: str, loader: str) -> str:
    """Подпись загрузчика до обращения к сети."""
    if loader == "vanilla":
        return "Vanilla"
    name = LOADER_BY_ID.get(loader, {}).get("label", loader)
    return name


# ------------------------------------------------------------- на диске

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _version_dirs(data_dir: Path) -> list[Path]:
    root = Path(data_dir) / "versions"
    if not root.is_dir():
        return []
    return [entry for entry in root.iterdir() if entry.is_dir()]


def loader_of_json(vjson: dict) -> str:
    """Определяет загрузчик по описанию версии."""
    ident = str(vjson.get("id") or "").lower()
    main = str(vjson.get("mainClass") or "").lower()
    names = " ".join(str((lib.get("name") or "")) for lib in vjson.get("libraries") or []).lower()
    if "neoforge" in ident or "neoforge" in names:
        return "neoforge"
    if "fabric" in ident or "fabric" in names or "knot" in main:
        return "fabric"
    if "forge" in ident or "minecraftforge" in names:
        return "forge"
    return "vanilla"


def find_installed(data_dir: Path, game: str, loader: str = "vanilla") -> str | None:
    """Настоящее имя папки установленной версии (у Forge/NeoForge его задаёт установщик)."""
    for entry in _version_dirs(data_dir):
        vjson = _read_json(entry / f"{entry.name}.json")
        if not vjson:
            continue
        parent = vjson.get("inheritsFrom")
        if loader == "vanilla":
            if entry.name == game and (entry / f"{game}.jar").is_file():
                return entry.name
            continue
        if parent != game:
            continue
        if loader_of_json(vjson) != loader:
            continue
        jar_name = str(vjson.get("jar") or parent)
        if not (Path(data_dir) / "versions" / jar_name / f"{jar_name}.jar").is_file():
            continue
        return entry.name
    return None


def installed_versions(data_dir: Path) -> list[dict]:
    """Всё, что реально лежит в versions/: для страницы «Библиотека»."""
    result: list[dict] = []
    for entry in _version_dirs(data_dir):
        vjson = _read_json(entry / f"{entry.name}.json")
        if not vjson:
            continue
        parent = vjson.get("inheritsFrom")
        loader = loader_of_json(vjson)
        jar_name = str(vjson.get("jar") or parent or entry.name)
        jar = Path(data_dir) / "versions" / jar_name / f"{jar_name}.jar"
        if not jar.is_file():
            continue
        size = versions.version_size(data_dir, entry.name)
        if parent:
            size += versions.version_size(data_dir, str(parent))
        result.append(
            {
                "id": entry.name,
                "game": parent or entry.name,
                "loader": loader,
                "label": LOADER_BY_ID.get(loader, {}).get("label", loader),
                "path": entry,
                "size": size,
            }
        )
    result.sort(key=lambda item: (item["game"], item["loader"]))
    return result
