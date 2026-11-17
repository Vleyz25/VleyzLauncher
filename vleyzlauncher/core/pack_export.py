"""Экспорт текущей сборки в .mrpack для Modrinth.

Файлы, которые Modrinth знает по SHA1, попадают в индекс со ссылками на
скачивание. Остальные (самодельные, локальные) кладутся в overrides/mods,
чтобы пак не потерял моды. Сеть нужна только для опознания файлов.
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

from . import mods as mods_core, modupdates, net

FORMAT_VERSION = 1


def _file_entry(version: dict, sha1: str, filename: str, size: int) -> dict | None:
    files = version.get("files") or []
    chosen = None
    for item in files:
        if not isinstance(item, dict):
            continue
        if (item.get("hashes") or {}).get("sha1") == sha1 and item.get("url"):
            chosen = item
            break
    if chosen is None:
        for item in files:
            if isinstance(item, dict) and item.get("url"):
                chosen = item
                break
    if chosen is None:
        return None
    hashes = chosen.get("hashes") or {}
    return {
        "path": f"mods/{filename}",
        "hashes": {
            "sha1": str(hashes.get("sha1") or sha1),
            "sha512": str(hashes.get("sha512") or ""),
        },
        "downloads": [str(chosen["url"])],
        "fileSize": int(chosen.get("size") or size or 0),
    }


def export(data_dir, instance_id: str, target: Path, game: str = "",
           loader: str = "", log=None) -> dict:
    log = log or (lambda message: None)
    target = Path(target)
    source = mods_core.mods_dir(Path(data_dir), instance_id)
    jars = sorted(path for path in source.glob("*.jar") if path.is_file()) \
        if source.is_dir() else []
    files: list[dict] = []
    overrides: list[Path] = []
    for jar in jars:
        sha1 = net.sha1_file(jar) or ""
        version = None
        try:
            version = modupdates.version_by_hash(sha1) if sha1 else None
        except modupdates.ModUpdateError as exc:
            log(f"{jar.name}: Modrinth недоступен ({exc}), кладу в overrides")
        entry = _file_entry(version, sha1, jar.name, jar.stat().st_size) if version else None
        if entry is None:
            overrides.append(jar)
        else:
            files.append(entry)

    index = {
        "formatVersion": FORMAT_VERSION,
        "game": "minecraft",
        "versionId": f"{instance_id}-{time.strftime('%Y%m%d%H%M')}",
        "name": instance_id,
        "summary": "Собрано Vleyz Launcher",
        "files": files,
        "dependencies": ({"minecraft": game} if game else {}),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("modrinth.index.json",
                         json.dumps(index, ensure_ascii=False, indent=2))
        for jar in overrides:
            archive.write(jar, f"overrides/mods/{jar.name}")
    return {
        "files": len(files),
        "overrides": len(overrides),
        "total": len(jars),
        "path": str(target),
    }
