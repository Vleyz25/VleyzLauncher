from __future__ import annotations

import json
import time
from pathlib import Path

from . import net

MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
MANIFEST_TTL = 30 * 60
OS_NAME = "windows"
ARCH = "x64"

# Плейсхолдеры, которые Mojang подставляет в аргументы
PLACEHOLDERS = ("${",)


class VersionError(Exception):
    pass


def manifest(data_dir: Path, force: bool = False) -> dict:
    """Манифест версий Mojang с кэшем на 30 минут."""
    cache = Path(data_dir) / "versions" / "version_manifest_v2.json"
    if cache.is_file() and not force:
        try:
            if time.time() - cache.stat().st_mtime < MANIFEST_TTL:
                return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    data = net.fetch_json(MANIFEST_URL)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def manifest_entry(data_dir: Path, version_id: str, force: bool = False) -> dict:
    """Запись версии из манифеста. При промахе обновляет кэш и ищет снова."""
    data = manifest(data_dir, force=force)
    for version in data.get("versions", []):
        if version.get("id") == version_id:
            return version
    if not force:
        return manifest_entry(data_dir, version_id, force=True)
    raise VersionError(f"Версия {version_id} не найдена в официальном манифесте Mojang")


def _features_match(rule_features, features: dict) -> bool:
    for key, expected in (rule_features or {}).items():
        if bool(features.get(key, False)) != bool(expected):
            return False
    return True


def _rule_matches(rule: dict, features: dict) -> bool:
    os_rule = rule.get("os")
    if os_rule:
        if "name" in os_rule and os_rule["name"] != OS_NAME:
            return False
        if "arch" in os_rule:
            arch = os_rule["arch"]
            if arch == "x86" and ARCH == "x64":
                return False
            if arch in ("arm64", "aarch64") and ARCH != "arm64":
                return False
    return _features_match(rule.get("features"), features)


def rules_allow(rules: list | None, features: dict | None = None) -> bool:
    """Правила Mojang. features по умолчанию пустые: quickPlay и demo не активны."""
    if not rules:
        return True
    active = features or {}
    allowed = False
    for rule in rules:
        if _rule_matches(rule, active):
            allowed = rule.get("action") == "allow"
    return allowed


def version_json(data_dir: Path, version_id: str) -> dict:
    path = Path(data_dir) / "versions" / version_id / f"{version_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Нет описания версии {version_id}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise VersionError(f"Описание версии {version_id} повреждено: {exc}") from exc


def ensure_version_json(data_dir: Path, version_id: str, cancel=None) -> dict:
    """Описание версии: с диска или скачивается из манифеста (без установки игры)."""
    path = Path(data_dir) / "versions" / version_id / f"{version_id}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            path.unlink(missing_ok=True)
    entry = manifest_entry(data_dir, version_id)
    net.download(entry["url"], path, entry.get("sha1"), entry.get("size"), cancel=cancel)
    return json.loads(path.read_text(encoding="utf-8"))


def version_meta(data_dir: Path, version_id: str, cancel=None) -> dict:
    """Описание версии только для справки (Java, дата): в отдельном кэше, не в versions.

    Так папка versions остаётся списком реально установленных версий.
    """
    cache = Path(data_dir) / "cache" / "meta" / f"{version_id}.json"
    if cache.is_file():
        try:
            if time.time() - cache.stat().st_mtime < MANIFEST_TTL * 8:
                return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    entry = manifest_entry(data_dir, version_id)
    net.download(entry["url"], cache, entry.get("sha1"), entry.get("size"), cancel=cancel)
    return json.loads(cache.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ java

def java_requirement(data_dir: Path, version_id: str, cancel=None) -> int:
    """Сколько Java нужно версии (справочный кэш, игру не устанавливает)."""
    try:
        vjson = version_json(data_dir, version_id)
    except (FileNotFoundError, ValueError):
        try:
            vjson = version_meta(data_dir, version_id, cancel=cancel)
        except (net.DownloadError, VersionError, OSError, ValueError):
            return 8
    return java_requirement_from(vjson)


def java_requirement_from(vjson: dict) -> int:
    required = (vjson.get("javaVersion") or {}).get("majorVersion")
    try:
        return int(required)
    except (TypeError, ValueError):
        return 8


# --------------------------------------------------------------- библиотеки

def maven_path(name: str) -> str | None:
    """group:artifact:version[:classifier][:extension] -> путь в maven-репозитории."""
    parts = (name or "").split(":")
    if len(parts) < 3:
        return None
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 and parts[3] else ""
    extension = parts[4] if len(parts) > 4 and parts[4] else "jar"
    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += f".{extension}"
    return "/".join(group.split(".") + [artifact, version, filename])


def _lib_key(lib: dict) -> str:
    parts = (lib.get("name") or "").split(":")
    if len(parts) >= 2:
        classifier = parts[3] if len(parts) > 3 else ""
        return f"{parts[0]}:{parts[1]}:{classifier}"
    return (lib.get("name") or id(lib)).strip()


def merge_libraries(parent: list | None, child: list | None) -> list:
    """Слияние библиотек: дочерние идут последними и перекрывают те же координаты."""
    combined = list(parent or []) + list(child or [])
    seen: set[str] = set()
    result: list = []
    for lib in reversed(combined):
        if not isinstance(lib, dict):
            continue
        key = _lib_key(lib)
        if key in seen:
            continue
        seen.add(key)
        result.append(lib)
    result.reverse()
    return result


def resolved_json(data_dir: Path, version_id: str, _depth: int = 0) -> dict:
    """Описание версии с раскрытым inheritsFrom (ваниль + слой загрузчика)."""
    raw = version_json(data_dir, version_id)
    parent_id = raw.get("inheritsFrom")
    if not parent_id or _depth > 8:
        merged = dict(raw)
        merged.setdefault("id", version_id)
        return merged

    parent = resolved_json(data_dir, parent_id, _depth + 1)
    merged = dict(parent)
    merged["id"] = version_id
    merged["inheritsFrom"] = parent_id
    merged["jar"] = raw.get("jar") or parent.get("jar") or parent_id

    for key in ("mainClass", "type", "releaseTime", "time", "minimumLauncherVersion",
                "assets", "assetIndex", "downloads", "javaVersion"):
        if key in raw and raw[key]:
            merged[key] = raw[key]

    merged["libraries"] = merge_libraries(parent.get("libraries"), raw.get("libraries"))

    parent_args = parent.get("arguments") or {}
    child_args = raw.get("arguments") or {}
    merged["arguments"] = {
        "jvm": list(parent_args.get("jvm") or []) + list(child_args.get("jvm") or []),
        "game": list(parent_args.get("game") or []) + list(child_args.get("game") or []),
    }
    if raw.get("minecraftArguments"):
        merged["minecraftArguments"] = raw["minecraftArguments"]
    elif parent.get("minecraftArguments") and not child_args:
        merged["minecraftArguments"] = parent["minecraftArguments"]
    return merged


def library_path(lib: dict) -> str | None:
    """Куда библиотека ложится в libraries/ — есть даже у записей без URL."""
    downloads = lib.get("downloads") or {}
    artifact = downloads.get("artifact") or {}
    if artifact.get("path"):
        return str(artifact["path"])
    name = lib.get("name")
    return maven_path(name) if name else None


def library_source(lib: dict) -> tuple[str, str, str | None, int | None] | None:
    """(url, относительный путь, sha1, размер) — только если файл можно скачать."""
    downloads = lib.get("downloads") or {}
    artifact = downloads.get("artifact") or {}
    path = library_path(lib)
    if not path:
        return None
    if artifact.get("url"):
        return artifact["url"], path, artifact.get("sha1"), artifact.get("size")
    base = (lib.get("url") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/{path}", path, lib.get("sha1"), lib.get("size")


def library_classpath_entries(vjson: dict, libraries_dir: Path) -> list[Path]:
    """Файлы библиотек для -cp (только разрешённые для Windows, без нативов)."""
    result: list[Path] = []
    seen: set[str] = set()
    for lib in vjson.get("libraries", []):
        if not rules_allow(lib.get("rules")):
            continue
        relative = library_path(lib)
        if not relative:
            continue
        path = Path(libraries_dir) / relative
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def client_jar_id(vjson: dict, version_id: str) -> str:
    return str(vjson.get("jar") or version_id)


def libraries_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "libraries"


def assets_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "assets"


def releases(data_dir: Path, force: bool = False) -> list[dict]:
    """Все официальные релизы Mojang, от новых к старым."""
    data = manifest(data_dir, force=force)
    result = [
        version
        for version in data.get("versions", [])
        if version.get("type") == "release" and version.get("id")
    ]
    result.sort(key=lambda v: v.get("releaseTime", ""), reverse=True)
    return result


def version_date(data_dir: Path, version_id: str) -> str:
    try:
        entry = manifest_entry(data_dir, version_id)
    except (net.DownloadError, VersionError):
        return ""
    return (entry.get("releaseTime") or "")[:10]


def version_size(data_dir: Path, version_id: str) -> int:
    """Размер папки версии на диске."""
    root = Path(data_dir) / "versions" / version_id
    total = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total
