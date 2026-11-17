"""Метаданные модов из .jar: id, имя, версия, зависимости.

Нужны, чтобы ловить дубли (один мод двумя файлами) и недостающие
зависимости без запуска игры. Читаем fabric.mod.json, quilt.mod.json и
META-INF/mods.toml; остальные файлы просто пропускаем.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

FABRIC = "fabric.mod.json"
QUILT = "quilt.mod.json"
TOMLS = ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
BUILTIN = {
    "minecraft", "java", "fabricloader", "fabric_loader", "forge", "neoforge",
    "quilt_loader", "quilted_fabric_api", "mixinextras",
}
MOD_ID = re.compile(r'modId\s*=\s*"([^"]+)"')
DISPLAY = re.compile(r'displayName\s*=\s*"([^"]+)"')
VERSION = re.compile(r'version\s*=\s*"([^"]+)"')


def _read_json(archive: zipfile.ZipFile, name: str) -> dict | None:
    try:
        data = json.loads(archive.read(name).decode("utf-8", "replace"))
    except (KeyError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _fabric(archive: zipfile.ZipFile, name: str) -> dict | None:
    data = _read_json(archive, name)
    if not data:
        return None
    loader = data.get("quilt_loader") if name == QUILT else None
    if isinstance(loader, dict):
        mod_id = loader.get("id")
        version = loader.get("version")
        depends_raw = loader.get("depends") or []
        depends = [
            str(item.get("id")) for item in depends_raw
            if isinstance(item, dict) and item.get("id")
        ]
        name_value = data.get("name") or mod_id
    else:
        mod_id = data.get("id")
        version = data.get("version")
        depends_map = data.get("depends") or {}
        depends = [str(key) for key in depends_map] if isinstance(depends_map, dict) else []
        name_value = data.get("name") or mod_id
    if not mod_id:
        return None
    return {
        "id": str(mod_id),
        "name": str(name_value or mod_id),
        "version": str(version or ""),
        "depends": depends,
        "source": name,
    }


def _toml(archive: zipfile.ZipFile, name: str) -> dict | None:
    try:
        text = archive.read(name).decode("utf-8", "replace")
    except (KeyError, OSError):
        return None
    block = text.split("[[mods]]", 1)[1] if "[[mods]]" in text else text
    mod_id = MOD_ID.search(block)
    if not mod_id:
        return None
    display = DISPLAY.search(block)
    version = VERSION.search(block)
    return {
        "id": mod_id.group(1),
        "name": display.group(1) if display else mod_id.group(1),
        "version": version.group(1) if version else "",
        "depends": [],
        "source": name,
    }


def read(path) -> dict | None:
    """Метаданные одного jar. None — это не мод или файл битый."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for candidate in (FABRIC, QUILT):
                if candidate in names:
                    record = _fabric(archive, candidate)
                    if record:
                        record["file"] = path.name
                        return record
            for candidate in TOMLS:
                if candidate in names:
                    record = _toml(archive, candidate)
                    if record:
                        record["file"] = path.name
                        return record
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def scan(folder) -> list[dict]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    records: list[dict] = []
    for path in sorted(folder.glob("*.jar")):
        record = read(path)
        if record:
            records.append(record)
    return records


def duplicates(records: list[dict]) -> list[dict]:
    """Моды, у которых несколько файлов с одним id."""
    groups: dict[str, list[str]] = {}
    for record in records:
        mod_id = str(record.get("id") or "")
        if mod_id:
            groups.setdefault(mod_id, []).append(str(record.get("file") or ""))
    return [
        {"id": mod_id, "files": files, "name": records[0].get("name") if records else mod_id}
        for mod_id, files in groups.items() if len(files) > 1
    ]


def missing_dependencies(records: list[dict]) -> list[dict]:
    """Обязательные зависимости, которых нет среди установленных jar."""
    installed = {str(record.get("id")) for record in records}
    missing: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        for dependency in record.get("depends") or []:
            dep = str(dependency)
            if dep in installed or dep in BUILTIN:
                continue
            key = (str(record.get("id")), dep)
            if key in seen:
                continue
            seen.add(key)
            missing.append({"mod": record.get("name") or record.get("id"),
                            "id": record.get("id"), "dep": dep})
    return missing
