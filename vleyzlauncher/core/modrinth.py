"""Клиент Modrinth API: поиск модов и установка с зависимостями."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from . import net, safepath

API = "https://api.modrinth.com/v2"
PAGE_LIMIT = 12
MAX_DEPTH = 64


class ModrinthError(Exception):
    pass


_project_cache: dict[str, dict] = {}


def _call(path: str, params: dict | None = None):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return net.fetch_json(url, timeout=25, retries=3)
    except net.DownloadError as exc:
        raise ModrinthError(f"Modrinth недоступен: {exc}") from exc


def _loader_id(loader: str) -> str:
    value = str(loader or "").strip().lower()
    if value not in ("fabric", "forge", "neoforge"):
        raise ModrinthError("Моды Modrinth доступны для Fabric, Forge или NeoForge")
    return value


def search(
    query: str,
    game_version: str,
    loader: str,
    limit: int = PAGE_LIMIT,
    offset: int = 0,
) -> list[dict]:
    """Ищет моды, совместимые с версией игры и загрузчиком."""
    loader = _loader_id(loader)
    return _search(
        query,
        [["project_type:mod"], [f"versions:{game_version}"], [f"categories:{loader}"]],
        limit,
        offset,
    )


def search_type(
    query: str,
    game_version: str,
    project_type: str,
    limit: int = PAGE_LIMIT,
    offset: int = 0,
    loader: str | None = None,
) -> list[dict]:
    """Поиск ресурспаков и шейдеров: загрузчик для них не нужен."""
    if project_type not in ("resourcepack", "shader", "mod"):
        raise ModrinthError(f"Неизвестный тип проекта: {project_type}")
    facets = [["project_type:" + project_type]]
    if game_version:
        facets.append([f"versions:{game_version}"])
    if loader:
        facets.append([f"categories:{_loader_id(loader)}"])
    return _search(query, facets, limit, offset)


def _search(query: str, facets: list[list[str]], limit: int, offset: int) -> list[dict]:
    data = _call(
        "/search",
        {
            "query": query or "",
            "limit": max(1, min(50, int(limit))),
            "offset": max(0, int(offset)),
            "index": "relevance",
            "facets": json.dumps(facets, separators=(",", ":")),
        },
    )
    hits = data.get("hits") if isinstance(data, dict) else None
    if not isinstance(hits, list):
        return []
    result: list[dict] = []
    for hit in hits:
        if not isinstance(hit, dict) or not hit.get("project_id"):
            continue
        result.append(
            {
                "id": str(hit.get("project_id")),
                "slug": str(hit.get("slug") or ""),
                "title": str(hit.get("title") or hit.get("slug") or "Mod"),
                "description": str(hit.get("description") or ""),
                "downloads": int(hit.get("downloads") or 0),
                "icon_url": str(hit.get("icon_url") or ""),
                "author": str(hit.get("author") or ""),
                "url": f"https://modrinth.com/{hit.get('project_type') or 'mod'}/"
                       f"{hit.get('slug') or hit.get('project_id')}",
            }
        )
    return result


def project(project_id: str) -> dict:
    key = str(project_id or "")
    if key in _project_cache:
        return _project_cache[key]
    data = _call(f"/project/{urllib.parse.quote(key)}")
    if not isinstance(data, dict):
        data = {}
    _project_cache[key] = data
    return data


def title(project_id: str) -> str:
    try:
        data = project(project_id)
    except ModrinthError:
        return str(project_id)
    return str(data.get("title") or data.get("slug") or project_id)


def versions(project_id: str, game_version: str, loader: str) -> list[dict]:
    loader = _loader_id(loader)
    data = _call(
        f"/project/{urllib.parse.quote(str(project_id))}/version",
        {
            "game_versions": json.dumps([game_version], separators=(",", ":")),
            "loaders": json.dumps([loader], separators=(",", ":")),
        },
    )
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def version(version_id: str) -> dict:
    data = _call(f"/version/{urllib.parse.quote(str(version_id))}")
    return data if isinstance(data, dict) else {}


def _sort_key(item: dict) -> tuple:
    release = 1 if item.get("version_type") == "release" else 0
    return release, str(item.get("date_published") or "")


def pick_version(project_id: str, game_version: str, loader: str) -> dict | None:
    items = versions(project_id, game_version, loader)
    if not items:
        return None
    return max(items, key=_sort_key)


def versions_for(project_id: str, game_version: str) -> list[dict]:
    """Версии проекта без фильтра по загрузчику — для ресурспаков и шейдеров."""
    params: dict = {}
    if game_version:
        params["game_versions"] = json.dumps([game_version], separators=(",", ":"))
    data = _call(f"/project/{urllib.parse.quote(str(project_id))}/version", params or None)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def pick_pack_version(project_id: str, game_version: str) -> dict | None:
    items = versions_for(project_id, game_version)
    if not items:
        return None
    return max(items, key=_sort_key)


def _primary_file(item: dict) -> dict | None:
    files = item.get("files") or []
    if not isinstance(files, list):
        return None
    usable = [file for file in files if isinstance(file, dict) and file.get("url")]
    if not usable:
        return None
    return next((file for file in usable if file.get("primary")), usable[0])


def _entry(item: dict, dependency: bool) -> dict:
    file = _primary_file(item)
    if not file:
        raise ModrinthError(f"У версии {item.get('version_number') or item.get('id')} нет файла")
    hashes = file.get("hashes") or {}
    project_id = str(item.get("project_id") or "")
    filename = Path(str(file.get("filename") or "mod.jar")).name
    return {
        "project_id": project_id,
        "title": title(project_id),
        "version_id": str(item.get("id") or ""),
        "version_number": str(item.get("version_number") or ""),
        "filename": filename,
        "url": str(file["url"]),
        "size": int(file.get("size") or 0),
        "sha1": str(hashes.get("sha1") or "") or None,
        "sha512": str(hashes.get("sha512") or "") or None,
        "dependency": dependency,
        "game_versions": list(item.get("game_versions") or []),
        "loaders": list(item.get("loaders") or []),
    }


def resolve(
    project_id: str,
    game_version: str,
    loader: str,
    include_optional: bool = False,
) -> list[dict]:
    """Возвращает мод и все обязательные зависимости.

    Порядок — сначала зависимости, затем сам мод: так удобнее показывать
    в логе установки. Циклы и повторные проекты отбрасываются.
    """
    loader = _loader_id(loader)
    result: list[dict] = []
    seen: set[str] = set()

    def add_version(item: dict, dependency: bool) -> None:
        if not item or len(result) >= MAX_DEPTH:
            return
        pid = str(item.get("project_id") or "")
        marker = pid or f"version:{item.get('id')}"
        if marker in seen:
            return
        seen.add(marker)
        for dep in item.get("dependencies") or []:
            if not isinstance(dep, dict):
                continue
            kind = str(dep.get("dependency_type") or "")
            if kind != "required" and not (include_optional and kind == "optional"):
                continue
            dep_version_id = str(dep.get("version_id") or "")
            if dep_version_id:
                add_version(version(dep_version_id), True)
            elif dep.get("project_id"):
                add_project(str(dep["project_id"]), True)
        result.append(_entry(item, dependency))

    def add_project(pid: str, dependency: bool) -> None:
        if not pid or pid in seen or len(result) >= MAX_DEPTH:
            return
        item = pick_version(pid, game_version, loader)
        if not item:
            if dependency:
                raise ModrinthError(
                    f"Нет версии зависимости {title(pid)} для {game_version} / {loader}"
                )
            raise ModrinthError(f"Нет версии для {game_version} / {loader}")
        add_version(item, dependency)

    add_project(str(project_id), False)
    if not result:
        raise ModrinthError(f"Нет версии для {game_version} / {loader}")
    return result


def install(
    files: list[dict],
    destination: Path,
    log=None,
    cancel=None,
    on_progress=None,
) -> dict:
    """Скачивает файлы в папку модов. Уже целые файлы пропускает."""
    log = log or (lambda message: None)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    safepath.require_space(destination, sum(int(item.get("size") or 0) for item in files))
    installed: list[str] = []
    skipped: list[str] = []
    total = len(files)
    for index, item in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            raise net.DownloadCancelled()
        filename = safepath.sanitize_name(
            Path(str(item.get("filename") or "mod.jar")).name, "mod.jar"
        )
        if not filename:
            continue
        target = destination / filename
        if net.file_is_valid(target, item.get("sha1"), item.get("size") or None):
            skipped.append(filename)
            log(f"[{index}/{total}] уже установлен: {filename}")
            if on_progress is not None:
                on_progress(index, total, item)
            continue
        log(f"[{index}/{total}] скачиваю {item.get('title') or filename}")
        net.download(
            str(item["url"]),
            target,
            item.get("sha1"),
            int(item.get("size") or 0) or None,
            cancel=cancel,
            sha512=item.get("sha512") or None,
        )
        installed.append(filename)
        if on_progress is not None:
            on_progress(index, total, item)
    return {"installed": installed, "skipped": skipped, "total": total}
