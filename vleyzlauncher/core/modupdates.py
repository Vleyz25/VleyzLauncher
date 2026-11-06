"""Проверка обновлений установленных модов через Modrinth.

Файл мода определяется по SHA1: Modrinth сам говорит, какой проект и версия
ему соответствуют. Если хеша в базе нет (мод не с Modrinth, старая сборка),
такой файл просто пропускается — трогать его мы не имеем права.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from . import modrinth, mods, net

log = logging.getLogger(__name__)

API = "https://api.modrinth.com/v2"
MAX_CHECK = 120


class ModUpdateError(Exception):
    pass


def _json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": net.USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ModUpdateError(f"Modrinth ответил {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - сеть бывает любой
        raise ModUpdateError(f"Modrinth недоступен: {exc}") from exc


def version_by_hash(sha1: str) -> dict | None:
    """Версия Modrinth по SHA1 файла или None, если файла там нет."""
    if not sha1:
        return None
    data = _json(f"{API}/version_file/{sha1}?algorithm=sha1")
    return data if isinstance(data, dict) else None


def _entry(filename: str, current: dict, latest: dict) -> dict:
    file = modrinth._primary_file(latest) or {}
    hashes = file.get("hashes") or {}
    project_id = str(latest.get("project_id") or "")
    return {
        "filename": filename,
        "project_id": project_id,
        "title": modrinth.title(project_id) if project_id else filename,
        "current_version_id": str(current.get("id") or ""),
        "current_version": str(current.get("version_number") or ""),
        "latest_version_id": str(latest.get("id") or ""),
        "latest_version": str(latest.get("version_number") or ""),
        "url": str(file.get("url") or ""),
        "filename_new": Path(str(file.get("filename") or filename)).name,
        "sha1": str(hashes.get("sha1") or "") or None,
        "size": int(file.get("size") or 0) or None,
    }


def check(
    data_dir: Path,
    instance_id: str,
    game_version: str,
    loader: str,
    limit: int = MAX_CHECK,
) -> dict:
    """Сравнивает установленные моды с последними версиями для этой сборки."""
    items = mods.list_mods(data_dir, instance_id)[: max(1, int(limit))]
    updates: list[dict] = []
    unknown = 0
    errors = 0
    for item in items:
        path: Path = item["path"]
        try:
            sha1 = net.sha1_file(path)
            current = version_by_hash(sha1)
            if not current:
                unknown += 1
                continue
            project_id = str(current.get("project_id") or "")
            latest = modrinth.pick_version(project_id, game_version, loader) if project_id else None
            if not latest:
                unknown += 1
                continue
            if str(latest.get("id") or "") == str(current.get("id") or ""):
                continue
            entry = _entry(path.name, current, latest)
            if not entry["url"]:
                unknown += 1
                continue
            updates.append(entry)
        except (ModUpdateError, modrinth.ModrinthError) as exc:
            log.info("проверка %s не удалась: %s", path.name, exc)
            errors += 1
        except OSError:
            continue
    updates.sort(key=lambda entry: entry["title"].lower())
    return {
        "updates": updates,
        "checked": len(items),
        "unknown": unknown,
        "errors": errors,
    }


def apply(
    data_dir: Path,
    instance_id: str,
    entry: dict,
    log_fn=None,
    cancel=None,
) -> str:
    """Ставит новую версию мода вместо старой. Возвращает имя нового файла."""
    log_fn = log_fn or (lambda message: None)
    folder = mods.mods_dir(Path(data_dir), instance_id)
    folder.mkdir(parents=True, exist_ok=True)
    old_name = str(entry.get("filename") or "")
    new_name = Path(str(entry.get("filename_new") or old_name)).name
    if not new_name:
        raise ModUpdateError("У обновления нет имени файла")
    target = folder / new_name
    log_fn(f"Обновляю {entry.get('title') or old_name}: "
           f"{entry.get('current_version') or '?'} → {entry.get('latest_version') or '?'}")
    net.download(
        str(entry["url"]),
        target,
        entry.get("sha1"),
        entry.get("size"),
        cancel=cancel,
    )
    if old_name and old_name != new_name:
        old_path = folder / old_name
        if old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                disabled = mods.disabled_dir(Path(data_dir), instance_id)
                disabled.mkdir(parents=True, exist_ok=True)
                try:
                    old_path.replace(disabled / old_name)
                except OSError:
                    log.warning("старый файл %s не удалился", old_path)
    return new_name
