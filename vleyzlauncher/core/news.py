"""Новости и патчноуты Mojang с официального launchercontent.

Официальный лаунчер показывает их на главной. Здесь — короткий список с
заголовком, датой и текстом. Ссылка ведёт на статью Minecraft.net.
"""

from __future__ import annotations

import html
import re
import time

from . import net

BASE = "https://launchercontent.mojang.com"
NEWS_URL = f"{BASE}/v2/news.json"
PATCH_URL = f"{BASE}/v2/javaPatchNotes.json"
CACHE_TTL = 600.0
LIMIT = 20

_TAG = re.compile(r"<[^>]+>")
_cache: dict[str, tuple[float, list[dict]]] = {}


class NewsError(RuntimeError):
    pass


def clean(value: object) -> str:
    text = _TAG.sub(" ", str(value or ""))
    return " ".join(html.unescape(text).split())


def _image(entry: dict) -> str:
    for key in ("newsPageImage", "playPageImage", "image"):
        image = entry.get(key)
        if isinstance(image, dict) and image.get("url"):
            url = str(image["url"])
            return url if url.startswith("http") else BASE + url
    return ""


def _date(value: object) -> str:
    return str(value or "")[:10]


def _parse_news(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        rows.append({
            "id": str(entry.get("id") or ""),
            "title": clean(entry.get("title")),
            "tag": clean(entry.get("tag")),
            "category": clean(entry.get("category")),
            "date": _date(entry.get("date")),
            "text": clean(entry.get("text")),
            "url": str(entry.get("readMoreLink") or ""),
            "image": _image(entry),
            "kind": "news",
        })
        if len(rows) >= LIMIT:
            break
    return rows


def _parse_patches(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        rows.append({
            "id": str(entry.get("id") or ""),
            "title": clean(entry.get("title")),
            "tag": clean(entry.get("type")),
            "category": str(entry.get("version") or ""),
            "date": _date(entry.get("date")),
            "text": clean(entry.get("shortText")),
            "url": "",
            "image": _image(entry),
            "kind": "patch",
        })
        if len(rows) >= LIMIT:
            break
    return rows


def _fetch(key: str, url: str, parser) -> list[dict]:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        payload = net.fetch_json(url, timeout=20, retries=2)
    except net.DownloadError as exc:
        raise NewsError(f"Mojang недоступен: {exc}") from exc
    rows = parser(payload if isinstance(payload, dict) else {})
    _cache[key] = (now, rows)
    return rows


def clear_cache() -> None:
    _cache.clear()


def news(limit: int = 6) -> list[dict]:
    return _fetch("news", NEWS_URL, _parse_news)[:max(1, int(limit))]


def patch_notes(limit: int = 6) -> list[dict]:
    return _fetch("patches", PATCH_URL, _parse_patches)[:max(1, int(limit))]
