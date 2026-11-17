"""Каталог модов Modrinth: поиск с фильтрами, карточки и кэш.

UI не ходит в сеть напрямую: страница просит у каталога готовую страницу
результатов (с total и числом страниц), а каталог сам собирает facets,
сортировку и хранит короткий кэш ответов, чтобы перерисовка окна не
превращалась в новый запрос.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse

from . import cache as cache_core
from . import modrinth, net

PAGE_SIZE = 12
CACHE_TTL = 300.0
STALE_TTL = 7 * 86400.0
MAX_PAGE_SIZE = 50

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("", "Все категории"),
    ("optimization", "Оптимизация"),
    ("technology", "Технологии"),
    ("magic", "Магия"),
    ("adventure", "Приключения"),
    ("decoration", "Декор"),
    ("worldgen", "Генерация мира"),
    ("mobs", "Мобы"),
    ("equipment", "Снаряжение"),
    ("food", "Еда"),
    ("storage", "Хранилища"),
    ("utility", "Утилиты"),
    ("library", "Библиотеки"),
    ("game-mechanics", "Механики"),
    ("management", "Управление"),
    ("transportation", "Транспорт"),
    ("social", "Социальное"),
    ("minigame", "Мини-игры"),
    ("economy", "Экономика"),
)

SORTS: tuple[tuple[str, str], ...] = (
    ("relevance", "По релевантности"),
    ("downloads", "По загрузкам"),
    ("follows", "По подпискам"),
    ("newest", "Новые"),
    ("updated", "Обновлённые"),
)

PACK_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("", "Все категории"),
    ("adventure", "Приключения"),
    ("combat", "Бой"),
    ("magic", "Магия"),
    ("technology", "Технологии"),
    ("optimization", "Оптимизация"),
    ("building", "Строительство"),
    ("decoration", "Декор"),
    ("quests", "Квесты"),
    ("storage", "Хранилища"),
    ("utility", "Утилиты"),
    ("multiplayer", "Мультиплеер"),
    ("exploration", "Исследование"),
)

ENVIRONMENTS: tuple[tuple[str, str], ...] = (
    ("", "Клиент и сервер"),
    ("client", "Только клиент"),
    ("server", "Только сервер"),
)

LOADER_TAGS = frozenset(
    {"fabric", "forge", "neoforge", "quilt", "babric", "liteloader", "rift", "risugami"}
)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_MD_IMAGE = re.compile(r"!\[[^\]]*]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)]\([^)]*\)")
_MD_HEAD = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_EMPH = re.compile(r"[*_`>]+")
_FORMAT = re.compile(r"§.")
_URL = re.compile(r"https?://\S+")

_STORE: cache_core.DiskCache | None = None


def clean_text(value: object) -> str:
    """Описание Modrinth: убираем HTML, картинки, ссылки и лишние пробелы."""
    text = str(value or "")
    text = _SCRIPT.sub(" ", text)
    text = _MD_IMAGE.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _URL.sub(" ", text)
    text = _MD_HEAD.sub("", text)
    text = _FORMAT.sub("", text)
    text = _MD_EMPH.sub(" ", text)
    return " ".join(text.split())


def _stale(key: str):
    """Старая копия для офлайна: неделя, а не пять минут."""
    global _STORE
    if _STORE is None:
        _STORE = cache_core.DiskCache()
    return _STORE.get(key, STALE_TTL)


def clear_cache() -> None:
    global _STORE
    if _STORE is None:
        _STORE = cache_core.DiskCache()
    _STORE.clear()


def _call(path: str, params: dict | None = None):
    """Ответ Modrinth бывает и объектом, и массивом — отдаём как есть."""
    url = f"{modrinth.API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return net.fetch_json(url, timeout=25, retries=3)
    except net.DownloadError as exc:
        raise modrinth.ModrinthError(f"Modrinth недоступен: {exc}") from exc


def _cached(key: str, loader) -> dict:
    """Сначала диск, потом сеть: каталог переживает перезапуск лаунчера."""
    global _STORE
    if _STORE is None:
        _STORE = cache_core.DiskCache()
    hit = _STORE.get(key, CACHE_TTL)
    if hit is not None:
        return hit
    payload = loader()
    _STORE.set(key, payload)
    return payload


def build_facets(
    game_version: str = "",
    loader: str = "",
    category: str = "",
    environment: str = "",
    project_type: str = "mod",
) -> list[list[str]]:
    facets: list[list[str]] = [[f"project_type:{project_type}"]]
    if game_version:
        facets.append([f"versions:{game_version}"])
    if loader and loader != "vanilla":
        facets.append([f"categories:{loader}"])
    if category:
        facets.append([f"categories:{category}"])
    if environment == "client":
        facets.append(["client_side:required", "client_side:optional"])
    elif environment == "server":
        facets.append(["server_side:required", "server_side:optional"])
    return facets


def _hit(hit: dict) -> dict:
    categories = hit.get("categories") or []
    if not isinstance(categories, list):
        categories = []
    return {
        "id": str(hit.get("project_id") or ""),
        "slug": str(hit.get("slug") or ""),
        "title": str(hit.get("title") or hit.get("slug") or "Мод"),
        "description": str(hit.get("description") or ""),
        "author": str(hit.get("author") or ""),
        "downloads": int(hit.get("downloads") or 0),
        "follows": int(hit.get("follows") or 0),
        "icon_url": str(hit.get("icon_url") or ""),
        "categories": [str(item) for item in categories],
        "client_side": str(hit.get("client_side") or ""),
        "server_side": str(hit.get("server_side") or ""),
        "type": str(hit.get("project_type") or "mod"),
        "url": f"https://modrinth.com/{hit.get('project_type') or 'mod'}/"
               f"{hit.get('slug') or hit.get('project_id')}",
    }


def search(
    query: str = "",
    game_version: str = "",
    loader: str = "",
    category: str = "",
    environment: str = "",
    sort: str = "relevance",
    page: int = 0,
    page_size: int = PAGE_SIZE,
    project_type: str = "mod",
) -> dict:
    """Страница каталога: элементы, всего найдено, число страниц."""
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size)))
    page = max(0, int(page))
    offset = page * page_size
    facets = build_facets(game_version, loader, category, environment, project_type)
    index = sort if sort in {key for key, _label in SORTS} else "relevance"
    params = {
        "query": query or "",
        "limit": page_size,
        "offset": offset,
        "index": index,
        "facets": json.dumps(facets, separators=(",", ":")),
    }
    key = "search:" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    offline = False
    try:
        data = _cached(key, lambda: _call("/search", params))
    except modrinth.ModrinthError:
        data = _stale(key)
        if data is None:
            raise
        offline = True
    if not isinstance(data, dict):
        data = {}
    hits = data.get("hits")
    if not isinstance(hits, list):
        hits = []
    total = int(data.get("total_hits") or 0)
    items = [_hit(hit) for hit in hits if isinstance(hit, dict) and hit.get("project_id")]
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
        "has_next": offset + len(items) < total,
        "offline": offline,
    }


def pack_category_label(slug: str) -> str:
    for key, label in PACK_CATEGORIES:
        if key == slug:
            return label
    return slug


def search_packs(
    query: str = "",
    game_version: str = "",
    loader: str = "",
    category: str = "",
    sort: str = "relevance",
    page: int = 0,
    page_size: int = 12,
) -> dict:
    """Каталог модпаков Modrinth."""
    return search(query, game_version, loader, category, "", sort, page, page_size,
                  project_type="modpack")


def details(project_id: str) -> dict:
    """Подробности проекта для карточки: описание, ссылки, галерея."""
    key = str(project_id or "")
    if not key:
        raise modrinth.ModrinthError("пустой id проекта")
    payload = _cached(f"project:{key}", lambda: _call(f"/project/{urllib.parse.quote(key)}"))
    gallery = []
    for shot in payload.get("gallery") or []:
        if isinstance(shot, dict) and shot.get("url"):
            gallery.append(str(shot["url"]))
    return {
        "id": key,
        "slug": str(payload.get("slug") or ""),
        "title": str(payload.get("title") or payload.get("slug") or key),
        "description": str(payload.get("description") or ""),
        "body": clean_text(payload.get("body") or ""),
        "author": "",
        "downloads": int(payload.get("downloads") or 0),
        "follows": int(payload.get("followers") or 0),
        "icon_url": str(payload.get("icon_url") or ""),
        "categories": [str(item) for item in payload.get("categories") or []],
        "client_side": str(payload.get("client_side") or ""),
        "server_side": str(payload.get("server_side") or ""),
        "source_url": str(payload.get("source_url") or ""),
        "issues_url": str(payload.get("issues_url") or ""),
        "wiki_url": str(payload.get("wiki_url") or ""),
        "discord_url": str(payload.get("discord_url") or ""),
        "license": str((payload.get("license") or {}).get("id") or ""),
        "gallery": gallery,
        "updated": str(payload.get("updated") or ""),
    }


def project_versions(project_id: str, game_version: str = "", loader: str = "") -> list[dict]:
    """Версии файла проекта для выбранной игры и загрузчика, новые сверху."""
    params: dict = {}
    if game_version:
        params["game_versions"] = json.dumps([game_version], separators=(",", ":"))
    if loader and loader not in ("vanilla", ""):
        params["loaders"] = json.dumps([loader], separators=(",", ":"))
    key = f"versions:{project_id}:{game_version}:{loader}"
    data = _cached(
        key,
        lambda: _call(f"/project/{urllib.parse.quote(str(project_id))}/version", params or None),
    )
    if isinstance(data, dict):
        items = data.get("versions")
    else:
        items = data
    if not isinstance(items, list):
        return []
    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        files = item.get("files") or []
        primary = None
        for file in files:
            if isinstance(file, dict) and file.get("url"):
                if file.get("primary"):
                    primary = file
                    break
                primary = primary or file
        result.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or item.get("version_number") or ""),
                "version_number": str(item.get("version_number") or ""),
                "type": str(item.get("version_type") or "release"),
                "date": str(item.get("date_published") or ""),
                "downloads": int(item.get("downloads") or 0),
                "game_versions": [str(v) for v in item.get("game_versions") or []],
                "loaders": [str(v) for v in item.get("loaders") or []],
                "filename": str((primary or {}).get("filename") or ""),
                "size": int((primary or {}).get("size") or 0),
                "url": str((primary or {}).get("url") or ""),
                "sha1": str(((primary or {}).get("hashes") or {}).get("sha1") or "") or None,
                "sha512": str(((primary or {}).get("hashes") or {}).get("sha512") or "") or None,
            }
        )
    result.sort(key=lambda row: row["date"], reverse=True)
    return result


def plan(project_id: str, game_version: str, loader: str) -> list[dict]:
    """Файлы для установки: мод и обязательные зависимости."""
    return modrinth.resolve(project_id, game_version, loader)


def category_label(slug: str) -> str:
    for key, label in CATEGORIES:
        if key == slug:
            return label
    return slug


def display_category(item: dict) -> str:
    """Первая осмысленная категория: загрузчики в подписи не показываем."""
    for slug in item.get("categories") or []:
        if slug not in LOADER_TAGS and slug not in ("", "mod"):
            return category_label(slug)
    return ""


def sort_label(key: str) -> str:
    for value, label in SORTS:
        if value == key:
            return label
    return key
